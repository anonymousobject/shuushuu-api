"""
Safe Markdown parser for user-generated content (private messages and comments).

Supports a minimal subset of Markdown features:
- **bold** and *italic*
- [link text](url)
- > blockquotes
- Line breaks

Security: All HTML is escaped, no arbitrary HTML/scripts allowed.
"""

import re
from html import escape
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """
    Check if a URL is safe to link to.

    Blocks javascript:, data:, and other dangerous protocols.
    """
    try:
        parsed = urlparse(url)
        # Allow http, https, and protocol-relative URLs
        # Block javascript:, data:, file:, etc.
        return parsed.scheme in ("http", "https", "") or url.startswith("//")
    except Exception:
        return False


def parse_markdown(text: str) -> str:
    """
    Parse safe Markdown subset to HTML.

    Supports:
    - **bold** → <strong>bold</strong>
    - *italic* → <em>italic</em>
    - [text](url) → <a href="url">text</a>
    - > quote → <blockquote>quote</blockquote>
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

    # First escape all HTML entities in the raw text
    text = escape(text)

    # Process blockquotes (must be done before line breaks)
    # Match lines starting with >
    lines = text.split("\n")
    processed_lines = []
    in_blockquote = False
    blockquote_content = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("&gt;"):
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

    # Process links [text](url)
    # Must be done before bold/italic to avoid conflicts
    def replace_link(match: re.Match[str]) -> str:
        link_text = match.group(1)
        url = match.group(2)

        # Validate URL safety
        if not is_safe_url(url):
            # If URL is unsafe, just return the text without linking
            return f"[{link_text}]({url})"

        # URL is already escaped by the initial escape() call, so unescape it
        url = url.replace("&amp;", "&")

        return f'<a href="{escape(url)}" rel="nofollow noopener" target="_blank">{link_text}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)

    # Process bold **text**
    # Use negative lookbehind/lookahead to avoid matching single * meant for italic
    text = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", text)

    # Process italic *text*
    # Avoid matching ** (already processed) or * at start of word boundaries
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"<em>\1</em>", text)

    # Convert line breaks to <br>
    # Preserve blockquote HTML by not adding <br> inside them
    text = re.sub(r"\n(?!</?blockquote>)", "<br>\n", text)

    return text


def strip_markdown(text: str) -> str:
    """
    Strip markdown formatting and return plain text.

    Useful for:
    - Generating plain text previews
    - Email notifications
    - Search indexing

    Args:
        text: Markdown formatted text

    Returns:
        Plain text with markdown formatting removed
    """
    if not text:
        return ""

    # Remove links but keep text: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove bold: **text** → text
    text = re.sub(r"\*\*([^*]+?)\*\*", r"\1", text)

    # Remove italic: *text* → text
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"\1", text)

    # Remove blockquote markers: > text → text
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    return text
