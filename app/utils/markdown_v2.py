"""
Version 2 of the markdown renderer.

New posts render with these rules. On text written with version 1's syntax
the output matches version 1, except where inline markup crosses from one
block into another: version 2 applies the inline rules to each block on its
own.

The source is escaped first. A block pass then sorts lines into blocks, and
an inline pass renders each block's text. HTML that later passes must not
touch is held in a token table behind placeholders, and the placeholders are
expanded last.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.utils.markdown_inline import escape_source, is_face_line, render_inline

# Placeholders are a private-use code point, the token's index, and another
# private-use code point. The source has both code points replaced, so only the
# renderer can write a placeholder.
_TOKEN_START = "\ue000"
_TOKEN_END = "\ue001"
_TOKEN = re.compile("(\ue000[0-9]+\ue001)")


class _Tokens:
    """
    Rendered HTML held out of later passes, each piece behind a placeholder.
    """

    def __init__(self) -> None:
        self._html: list[str] = []

    def add(self, html: str) -> str:
        """Store rendered HTML and return the placeholder that stands in for it."""
        self._html.append(html)
        return f"{_TOKEN_START}{len(self._html) - 1}{_TOKEN_END}"

    def expand(self, text: str) -> str:
        """Replace every placeholder with its HTML."""
        return _TOKEN.sub(lambda match: self._html[int(match.group()[1:-1])], text)


def _prepare(text: str) -> str:
    """
    Escape the source, remove placeholder code points, and normalise line endings.
    """
    text = escape_source(text)
    # After escaping, because unescaping a stored entity (&#xe000;) can
    # produce one of these code points
    text = text.replace(_TOKEN_START, "\ufffd").replace(_TOKEN_END, "\ufffd")
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class _Block:
    """
    Consecutive lines of one kind: plain text lines, or the content of > quote lines.
    """

    kind: Literal["text", "quote"]
    lines: list[str]


def _quote_content(line: str) -> str | None:
    """
    Return the content of a > quote line, or None if the line isn't one.
    """
    stripped = line.lstrip()
    if stripped.startswith("&gt;") and not is_face_line(stripped):
        return stripped[4:].lstrip()  # Remove &gt; (escaped >)
    return None


def _split_blocks(text: str) -> list[_Block]:
    """
    Block pass: sort the lines into blocks.
    """
    blocks: list[_Block] = []
    for line in text.split("\n"):
        quote_content = _quote_content(line)
        block = (
            _Block("text", [line]) if quote_content is None else _Block("quote", [quote_content])
        )
        if blocks and blocks[-1].kind == block.kind:
            blocks[-1].lines += block.lines
        else:
            blocks.append(block)
    return blocks


def _render_inline(text: str) -> str:
    """
    Inline pass: render_inline on each piece of text between placeholders.

    The tokens' HTML never passes through the inline rules, and bold, italics,
    and links can't start on one side of a token and end on the other.
    """
    pieces = _TOKEN.split(text)
    pieces[::2] = [render_inline(piece) for piece in pieces[::2]]
    return "".join(pieces)


def _render_block(block: _Block) -> str:
    if block.kind == "quote":
        # Consecutive quote lines join with spaces into one blockquote
        return f"<blockquote>{_render_inline(' '.join(block.lines))}</blockquote>"
    # The inline pass runs over the whole run of lines, so italics and links can
    # span a line break, then each line break becomes a <br>
    return _render_inline("\n".join(block.lines)).replace("\n", "<br>\n")


def _line_break_before(block: _Block) -> str:
    # The newline before a blockquote stays bare, as in version 1
    return "\n" if block.kind == "quote" else "<br>\n"


def _render_blocks(text: str) -> str:
    """
    Render prepared text with the block and inline passes, leaving tokens in place.
    """
    blocks = _split_blocks(text)
    parts = [_render_block(blocks[0])]
    for block in blocks[1:]:
        parts += [_line_break_before(block), _render_block(block)]
    return "".join(parts)


def render_v2(text: str | None, headings: bool) -> str:  # noqa: ARG001
    """
    Render text written with version 2's rules to safe HTML.

    Args:
        text: Raw markdown text from user
        headings: Whether ## and ### lines become headings, for forum posts.
            Version 2 has no heading rule yet, so this has no effect.

    Returns:
        Safe HTML string
    """
    if not text:
        return ""
    tokens = _Tokens()
    html = _render_blocks(_prepare(text))
    return tokens.expand(html)
