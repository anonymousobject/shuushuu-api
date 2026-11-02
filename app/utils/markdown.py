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
- Bare URLs auto-linked (https://example.com → clickable link)
- > blockquotes
- Line breaks
- Version 2 only: - and 1. lists, `code` spans, ``` code blocks, and (forum
  posts only) #, ## and ### headings. Version 1 rows keep the original rules.
- BBCode-style [quote="user"]...[/quote]
- BBCode-style [spoiler]...[/spoiler] (renders as <span class="spoiler">)

All HTML is escaped - no arbitrary HTML/scripts allowed.
"""

import re
from html import escape

from app.utils.markdown_v1 import render_v1
from app.utils.markdown_v2 import render_v2

# The rules an edited comment or forum post moves to. Raising it also needs
# the column's default raised, so new posts get the same rules.
CURRENT_MARKUP_VERSION = 2


def parse_markdown(text: str | None, version: int, headings: bool = False) -> str:
    """
    Parse safe Markdown subset to HTML, by the row's markup_version.

    Version 1 is the original renderer (see markdown_v1.render_v1) and ignores
    `headings`. Version 2 adds lists and code (see markdown_v2.render_v2);
    `headings` enables #, ## and ### headings and is passed only for forum posts.

    Raises:
        ValueError: if version is not 1 or 2.
    """
    if version == 1:
        return render_v1(text)
    if version == 2:
        return render_v2(text, headings)
    raise ValueError(f"Unknown markup_version: {version}")


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

    # Remove anonymous BBCode quotes: [quote]text[/quote] → text
    text = re.sub(r"\[quote\](.*?)\[/quote\]", r"\1", text, flags=re.DOTALL)

    # Remove spoilers: [spoiler]text[/spoiler] → text (iterative for nesting)
    for _ in range(10):
        new_text = re.sub(r"\[spoiler\](.*?)\[/spoiler\]", r"\1", text, flags=re.DOTALL)
        if new_text == text:
            break
        text = new_text

    # Remove links but keep text: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove bold: **text** → text
    text = re.sub(r"\*\*([^*]+?)\*\*", r"\1", text)

    # Remove italic: *text* → text
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"\1", text)

    # Remove blockquote markers: > text → text
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    return text
