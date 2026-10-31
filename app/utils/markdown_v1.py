"""
Version 1 of the markdown renderer.

Posts stored before markup versions existed keep rendering with these rules.
"""

import re
from html import escape

from app.utils.markdown_inline import escape_source, is_face_line, render_inline


def _sub_innermost_first(pattern: re.Pattern[str], replacement: str, text: str) -> str:
    """
    Replace every match of a tempered-token tag pattern, pass after pass.

    Each pass replaces the innermost tags, exposing the next level of nesting
    to the following pass. Stops when a pass changes nothing, or after 10
    passes so pathological nesting can't loop for long.
    """
    max_iterations = 10
    for _ in range(max_iterations):
        new_text = pattern.sub(replacement, text)
        if new_text == text:
            break
        text = new_text
    return text


def render_v1(text: str) -> str:
    """
    Parse safe Markdown subset to HTML.

    Supports:
    - **bold** → <strong>bold</strong>
    - *italic* → <em>italic</em>
    - [text](url) → <a href="url">text</a>
    - > quote → <blockquote>quote</blockquote>
    - [spoiler]text[/spoiler] → <span class="spoiler">text</span>
    - Line breaks → <br>

    Security features:
    - All text content is HTML-escaped
    - URLs are validated to prevent javascript: attacks
    - No arbitrary HTML tags allowed
    - No image embedding (prevents tracking pixels)

    Args:
        text: Raw markdown text from user

    Returns:
        Safe HTML string
    """
    if not text:
        return ""

    text = escape_source(text)

    # Process BBCode quotes recursively: [quote="username"]...[/quote]
    # Keep processing until no more quotes are found (handles nested quotes)
    max_iterations = 10  # Prevent infinite loops
    iterations = 0
    while "[quote=" in text and iterations < max_iterations:
        iterations += 1

        # Match [quote="..."] or [quote=&quot;...&quot;] followed by content and [/quote]
        # Non-greedy: the leftmost opener pairs with the first closer after it.
        # Balanced nesting renders the same either way, and legacy comments that
        # lost an inner quote's closer depend on this pairing to keep the outer
        # author's attribution.
        def replace_bbcode_quote(match: re.Match[str]) -> str:
            username = match.group(1) or match.group(2)
            username = username.replace("&quot;", '"')
            content = match.group(3)
            return f"<blockquote><small>{escape(username)} said:</small> {content}</blockquote>"

        # Only an opener before the last closer can match, so the search stops
        # there. Searching further would scan from every later opener to the end
        # of the text, which is quadratic in the number of unclosed openers.
        last_closer = text.rfind("[/quote]")
        if last_closer == -1:
            break
        search_end = last_closer + len("[/quote]")
        text = (
            re.sub(
                r'\[quote=(?:"([^"]*)"|&quot;([^&]*)&quot;)\](.*?)\[/quote\]',
                replace_bbcode_quote,
                text[:search_end],
                count=1,  # Replace one at a time
                flags=re.DOTALL,
            )
            + text[search_end:]
        )

    # Process anonymous BBCode quotes: [quote]...[/quote] (no username)
    # Same tempered token and innermost-first passes as the spoilers below, so
    # nested quotes keep their nesting and unclosed openers stay linear
    anonymous_quote_pattern = re.compile(
        r"\[quote\]((?:(?!\[quote\]|\[/quote\]).)*?)\[/quote\]",
        re.DOTALL,
    )
    text = _sub_innermost_first(anonymous_quote_pattern, r"<blockquote>\1</blockquote>", text)

    # Remove any unmatched [quote] or [/quote] tags (orphaned tags)
    text = text.replace("[quote]", "")
    text = text.replace("[/quote]", "")

    # Process BBCode spoilers: [spoiler]...[/spoiler]
    # Use iterative approach to handle nested spoilers correctly
    spoiler_pattern = re.compile(
        r"\[spoiler\]((?:(?!\[spoiler\]|\[/spoiler\]).)*?)\[/spoiler\]",
        re.DOTALL,
    )
    text = _sub_innermost_first(spoiler_pattern, r'<span class="spoiler">\1</span>', text)

    # Remove any unmatched [spoiler] or [/spoiler] tags
    text = text.replace("[spoiler]", "")
    text = text.replace("[/spoiler]", "")

    # Process markdown blockquotes (must be done after BBCode quotes)
    # Match lines starting with >
    lines = text.split("\n")
    processed_lines = []
    in_blockquote = False
    blockquote_content = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("&gt;") and not is_face_line(stripped):
            # Start or continue blockquote
            quote_text = stripped[4:].lstrip()  # Remove &gt; (escaped >)
            blockquote_content.append(quote_text)
            in_blockquote = True
        else:
            # End of blockquote if we were in one
            if in_blockquote:
                processed_lines.append(f"<blockquote>{' '.join(blockquote_content)}</blockquote>")
                blockquote_content = []
                in_blockquote = False
            processed_lines.append(line)

    # Handle any remaining blockquote at end
    if in_blockquote:
        processed_lines.append(f"<blockquote>{' '.join(blockquote_content)}</blockquote>")

    text = "\n".join(processed_lines)

    text = render_inline(text)

    # Convert line breaks to <br>
    # Preserve blockquote HTML by not adding <br> inside them
    text = re.sub(r"\n(?!</?blockquote>)", "<br>\n", text)

    return text
