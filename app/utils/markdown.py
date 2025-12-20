"""
Safe Markdown parser and input sanitization for user-generated content.

=== SECURITY MODEL ===

This module provides security mechanisms for different types of user content:

1. MARKDOWN FIELDS (comments, PM message bodies):
   - Use parse_markdown() for rendering
   - parse_markdown() internally escapes ALL HTML, then processes safe markdown
   - XSS protection is automatic at render time
   - Store raw user input in DB (no escaping needed)

2. PLAIN TEXT FIELDS (tags, captions, user profiles, PM subjects):
   - Store as plain text (trimmed whitespace only, no HTML escaping)
   - XSS protection handled by Svelte's safe template interpolation ({variable})
   - Defense in depth: Frontend auto-escapes, never uses innerHTML for user content
   - Database normalized via scripts/normalize_db_text.py

3. LEGACY PHP DATA:
   - Use normalize_legacy_entities() in output validators (mode="before")
   - Converts legacy HTML entities (&quot;, &amp;) to normal characters
   - Only for reading old data from PHP codebase, NOT for new user input
   - After migration complete, this can be removed

=== SUPPORTED MARKDOWN FEATURES ===

- **bold** and *italic*
- [link text](url)
- > blockquotes
- Line breaks
- BBCode-style [quote="user"]...[/quote]

All HTML is escaped - no arbitrary HTML/scripts allowed.
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

    # First normalize any HTML entities that may have been stored in the DB
    # (e.g. &quot;, &amp;, etc.) by unescaping them, then escape to ensure a
    # single correct encoding. This prevents double-encoding like
    # '&amp;quot;' showing up in the rendered HTML.
    from html import unescape as html_unescape

    text = html_unescape(text)

    # Then escape all HTML entities in the raw text
    text = escape(text)

    # Process BBCode quotes recursively: [quote="username"]...[/quote]
    # Keep processing until no more quotes are found (handles nested quotes)
    max_iterations = 10  # Prevent infinite loops
    iterations = 0
    while "[quote=" in text and iterations < max_iterations:
        iterations += 1

        # Match [quote="..."] or [quote=&quot;...&quot;] followed by content and [/quote]
        # Use non-greedy matching to get innermost quotes first
        def replace_bbcode_quote(match: re.Match[str]) -> str:
            username = match.group(1) or match.group(2)
            username = username.replace("&quot;", '"')
            content = match.group(3)
            return f"<blockquote><small>{escape(username)} said:</small> {content}</blockquote>"

        text = re.sub(
            r'\[quote=(?:"([^"]*)"|&quot;([^&]*)&quot;)\](.*?)\[/quote\]',
            replace_bbcode_quote,
            text,
            count=1,  # Replace one at a time, innermost first
            flags=re.DOTALL,
        )

    # Remove any unmatched closing [/quote] tags (orphaned closing tags)
    text = text.replace("[/quote]", "")

    # Process markdown blockquotes (must be done after BBCode quotes)
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


def clean_user_input(text: str) -> str:
    """
    DEPRECATED: Not currently used. HTML escaping is handled by frontend.

    This function was designed for backend HTML escaping of plain text fields,
    but we now use a plain text storage model where:
    - Input is stored as plain text (trimmed whitespace only)
    - XSS protection is handled by Svelte's safe template interpolation
    - Database has been normalized via scripts/normalize_db_text.py

    Kept for potential future use or if security model changes.

    Args:
        text: Raw user input text

    Returns:
        HTML-escaped text with whitespace trimmed

    Example:
        >>> clean_user_input("<script>alert('xss')</script>")
        "&lt;script&gt;alert('xss')&lt;/script&gt;"
    """
    if text is None:
        return text
    return escape(text).strip()


def normalize_legacy_entities(text: str | None) -> str | None:
    """
    Normalize HTML entities from legacy PHP database data.

    This function handles data migration from the old PHP codebase which
    stored user input as HTML-encoded entities (&quot;, &amp;, etc.).

    IMPORTANT: Use ONLY in output validators (mode="before") to handle
    legacy data when reading from the database. DO NOT use for new user input.

    Use this for:
    - Output validators on fields that existed in PHP codebase
    - Reading legacy data from database (mode="before")
    - One-time data migration scripts

    DO NOT use this for:
    - Input validation (use clean_user_input instead)
    - New fields that didn't exist in PHP

    Args:
        text: Text potentially containing HTML entities from legacy data

    Returns:
        Text with HTML entities decoded and whitespace trimmed

    Example:
        >>> normalize_legacy_entities("&quot;hello&quot; &amp; goodbye")
        '"hello" & goodbye'
    """
    if text is None:
        return text
    from html import unescape as html_unescape

    return html_unescape(text).strip()


def normalize_entities(text: str | None) -> str | None:
    """
    DEPRECATED: Use clean_user_input() or normalize_legacy_entities() instead.

    This function is kept for backward compatibility but should not be used
    in new code. It creates ambiguity about whether we're sanitizing input
    or normalizing legacy data.

    For new code:
    - Input sanitization: Use clean_user_input()
    - Legacy data normalization: Use normalize_legacy_entities()
    """
    return normalize_legacy_entities(text)


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

    # Remove BBCode quotes: [quote="user"]text[/quote] → text
    text = re.sub(
        r'\[quote=(?:"[^"]*"|&quot;[^&]*&quot;)\](.*?)\[/quote\]', r"\1", text, flags=re.DOTALL
    )

    # Remove links but keep text: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove bold: **text** → text
    text = re.sub(r"\*\*([^*]+?)\*\*", r"\1", text)

    # Remove italic: *text* → text
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"\1", text)

    # Remove blockquote markers: > text → text
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    return text
