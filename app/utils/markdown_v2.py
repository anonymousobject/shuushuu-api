"""
Version 2 of the markdown renderer.

New posts render with these rules. On text written with version 1's syntax
the output matches version 1, except:

- Inline markup can't cross from one block or container into another:
  version 2 applies the inline rules to each on its own.
- Unclosed and mis-nested quote and spoiler tags pair differently, and every
  unmatched tag is stripped.
- Quotes and spoilers nest to any depth, where version 1 stops at ten.
- A container opened at the start of a line starts a line inside it too, so
  a > right after the opening tag makes a nested quote.
- A quote's name is plain text: the inline rules don't apply to it, and it
  can't hold a container tag.

The source is escaped first. A container scan then renders each quote and
spoiler pair, a block pass sorts lines into blocks, and an inline pass
renders each block's text. HTML that later passes must not touch is held in
a token table behind placeholders, and the placeholders are expanded last.
"""

import re
from dataclasses import dataclass, field
from html import escape, unescape
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
        self._quotes: set[str] = set()  # Placeholders of quote containers

    def add(self, html: str, *, quote: bool = False) -> str:
        """
        Store rendered HTML and return the placeholder that stands in for it.

        quote marks the HTML as a quote container, which the newline rules
        treat like a blockquote.
        """
        placeholder = f"{_TOKEN_START}{len(self._html)}{_TOKEN_END}"
        self._html.append(html)
        if quote:
            self._quotes.add(placeholder)
        return placeholder

    def starts_quote(self, text: str, position: int = 0) -> bool:
        """Check if the placeholder of a quote container starts at text[position]."""
        match = _TOKEN.match(text, position)
        return match is not None and match.group() in self._quotes

    def expand(self, text: str) -> str:
        """
        Replace every placeholder with its HTML, and the placeholders in that HTML in turn.

        A stack of pieces still to emit stands in for recursion, so nesting
        depth is limited only by the input's length, and each token's HTML is
        split and copied once.
        """
        parts: list[str] = []
        pending = _TOKEN.split(text)[::-1]  # The next piece is last
        while pending:
            piece = pending.pop()
            if piece.startswith(_TOKEN_START):
                pending += _TOKEN.split(self._html[int(piece[1:-1])])[::-1]
            else:
                parts.append(piece)
        return "".join(parts)


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


def _split_blocks(text: str, starts_line: bool) -> list[_Block]:
    """
    Block pass: sort the lines into blocks.

    starts_line says whether the text starts a line of the source. A container
    opened mid-line doesn't, and its first line stays inline.
    """
    blocks: list[_Block] = []
    for number, line in enumerate(text.split("\n")):
        # Only a line that starts a line of the source gets the block rules
        line_start = number > 0 or starts_line
        quote_content = _quote_content(line) if line_start else None
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


def _line_breaks(text: str, tokens: _Tokens) -> str:
    """
    Turn each newline in a run of text lines into <br> and a newline.
    """
    # The newline before a quote container stays bare, as in version 1
    return re.sub(
        "\n", lambda match: "\n" if tokens.starts_quote(text, match.end()) else "<br>\n", text
    )


def _render_block(block: _Block, tokens: _Tokens) -> str:
    """
    Render one block with the inline pass, leaving placeholders in place.
    """
    if block.kind == "quote":
        # Consecutive quote lines join with spaces into one blockquote
        return f"<blockquote>{_render_inline(' '.join(block.lines))}</blockquote>"
    # The inline pass runs over the whole run of lines, so italics and links can
    # span a line break, then the line breaks are rendered
    return _line_breaks(_render_inline("\n".join(block.lines)), tokens)


def _line_break_before(block: _Block, tokens: _Tokens) -> str:
    """
    Return the newline that separates a block from the one before it.
    """
    # The newline before a blockquote or a quote container stays bare, as in
    # version 1
    if block.kind == "quote" or tokens.starts_quote(block.lines[0]):
        return "\n"
    return "<br>\n"


def _render_blocks(text: str, tokens: _Tokens, *, starts_line: bool) -> str:
    """
    Render prepared text with the block and inline passes, leaving placeholders in place.
    """
    blocks = _split_blocks(text, starts_line)
    parts = [_render_block(blocks[0], tokens)]
    for block in blocks[1:]:
        parts += [_line_break_before(block, tokens), _render_block(block, tokens)]
    return "".join(parts)


# Container tags on the escaped source. In [quote="name"] the quotes are
# escaped by then. A name stops at the first "&", as in version 1, and can't
# run into a container tag or a placeholder: a tag in what looks like a name
# is still a tag, so a spoiler's closer can't hide in one. Other brackets are
# fine, as some usernames have them.
_CONTAINER_TAG = re.compile(
    r"\[(?P<slash>/?)(?P<kind>quote|spoiler)\]"
    r"|\[quote=&quot;(?P<name>(?:(?!\[/?(?:quote|spoiler)[\]=])[^&\ue000])*)&quot;\]"
)


@dataclass
class _Open:
    """
    An entry on the container scan's stack, with the content after it so far.

    The root is at the bottom. Above it, each entry is an opening tag that
    hasn't been closed yet.
    """

    kind: Literal["root", "quote", "spoiler"]
    name: str | None  # A named quote's name, escaped
    # Whether the content starts a line: the root does, and an opening tag
    # does when only whitespace is before it on its line
    starts_line: bool
    content: list[str] = field(default_factory=list)  # Source text and placeholders
    # Whether only whitespace has followed the content's last line start so far
    at_line_start: bool = field(init=False)

    def __post_init__(self) -> None:
        self.at_line_start = self.starts_line

    def append(self, piece: str) -> None:
        """Add source text or a placeholder to the content."""
        self.content.append(piece)
        newline = piece.rfind("\n")
        tail = piece[newline + 1 :]  # The whole piece if it has no newline
        self.at_line_start = (newline != -1 or self.at_line_start) and not tail.strip()


def _strip_unmatched(stack: list[_Open], start: int, open_at: dict[str, list[int]]) -> None:
    """
    Strip the opening tags at stack[start:] as unmatched, merging their content,
    in order, into the entry below them.
    """
    for entry in stack[start:]:
        for piece in entry.content:
            stack[start - 1].append(piece)
        open_at[entry.kind].pop()
    del stack[start:]


def _render_container(pair: _Open, tokens: _Tokens) -> str:
    """
    Render a matched pair, running the block and inline passes over its
    content, and return the placeholder that stands in for it.
    """
    html = _render_blocks("".join(pair.content), tokens, starts_line=pair.starts_line)
    if pair.kind == "spoiler":
        return tokens.add(f'<span class="spoiler">{html}</span>')
    if html.endswith("<br>\n"):
        # The newline at the end of a quote stays bare, as in version 1. This
        # relies on rendered content ending in <br>\n exactly when the source
        # content ends in a newline; new block kinds must keep that true.
        html = html.removesuffix("<br>\n") + "\n"
    if pair.name is not None:
        # Unescape the name, then escape it once, as version 1 does
        html = f"<small>{escape(unescape(pair.name))} said:</small> {html}"
    return tokens.add(f"<blockquote>{html}</blockquote>", quote=True)


def _render_containers(text: str, tokens: _Tokens) -> str:
    """
    Container scan: replace each matched quote or spoiler pair with a
    placeholder, innermost first, and strip every unmatched tag.

    One pass over the tags, with a stack of the tags still open. A closing tag
    pairs with the nearest open tag of its kind, unless a spoiler opened after
    that tag is still open: then the closing tag is unmatched, so a spoiler is
    never cut short and its text never renders outside it. Tags still open
    between a closing tag and its partner are unmatched.

    Each pair is rendered as it closes, over content whose inner pairs are
    already placeholders, so nothing recurses. Each opening tag is pushed and
    popped once, each closing tag is looked at once, and each character is
    rendered once.

    A pair's content gets the block rules on its first line only if its
    opening tag starts a line, so a container opened mid-line stays inline.
    """
    stack = [_Open("root", None, starts_line=True)]
    # Stack positions of the open tags of each kind, so the nearest open tag
    # of a kind, and whether a spoiler is open above it, are found at once
    open_at: dict[str, list[int]] = {"quote": [], "spoiler": []}
    position = 0
    for match in _CONTAINER_TAG.finditer(text):
        stack[-1].append(text[position : match.start()])
        position = match.end()
        kind: Literal["quote", "spoiler"] = "spoiler" if match["kind"] == "spoiler" else "quote"
        if not match["slash"]:
            open_at[kind].append(len(stack))
            stack.append(_Open(kind, match["name"], starts_line=stack[-1].at_line_start))
            continue
        if not open_at[kind]:
            continue  # Nothing to close: the closing tag is unmatched
        partner = open_at[kind][-1]
        if open_at["spoiler"] and open_at["spoiler"][-1] > partner:
            continue  # A spoiler is open inside the partner: unmatched
        _strip_unmatched(stack, partner + 1, open_at)
        pair = stack.pop()
        open_at[kind].pop()
        stack[-1].append(_render_container(pair, tokens))
    stack[-1].append(text[position:])
    _strip_unmatched(stack, 1, open_at)
    return "".join(stack[0].content)


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
    text = _render_containers(_prepare(text), tokens)
    return tokens.expand(_render_blocks(text, tokens, starts_line=True))
