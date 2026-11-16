"""
Inline markdown rules shared by every markdown version.

Escaping the source, then links, bare URLs, bold and italics. Block-level
structure (quotes, spoilers, lists, code) lives in the per-version modules.
"""

import re
import unicodedata
from collections.abc import Callable
from html import escape, unescape
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


_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_FACE_LINE = re.compile(r"&gt;(?:&#?\w+;|[^\s&<\ue000]){0,3}&lt;")


# One percent-encoded UTF-8 multi-byte sequence. The lead byte decides how many
# continuation bytes (%80-%BF) follow, so a match never needs to backtrack.
_CONTINUATION = r"%[89ABab][0-9A-Fa-f]"
_PERCENT_UTF8 = re.compile(
    rf"%(?:[Cc][2-9A-Fa-f]|[Dd][0-9A-Fa-f]){_CONTINUATION}"
    rf"|%[Ee][0-9A-Fa-f](?:{_CONTINUATION}){{2}}"
    rf"|%[Ff][0-4](?:{_CONTINUATION}){{3}}"
)


def _decode_percent_utf8(match: re.Match[str]) -> str:
    try:
        decoded = bytes.fromhex(match.group(0).replace("%", "")).decode("utf-8")
    except UnicodeDecodeError:
        return match.group(0)  # Overlong, surrogate or out of range
    # The pattern matches one sequence, so decoded is one character
    if unicodedata.category(decoded)[0] in "CZ":
        return match.group(0)
    return decoded


def _decode_for_display(url: str) -> str:
    """
    Decode percent-encoded non-ASCII characters in a URL, for display only.

    Each valid UTF-8 multi-byte sequence that is one visible character is
    shown as that character; anything else stays exactly as typed. ASCII
    escapes (%20, %2F, %3C, ...) are never decoded, so the shown text still
    pastes back to the same URL and can't gain markup characters.

    Categories C* and Z* stay encoded: controls, bidi overrides and zero-width
    characters (Cf), unassigned and surrogates, and spaces. Private use (Co) is
    load-bearing for security: markdown_v2 uses U+E000 and U+E001 as token
    placeholders and strips them from the source, so decoding %EE%80%80 here
    would let a user forge one.
    """
    return _PERCENT_UTF8.sub(_decode_percent_utf8, url)


def is_face_line(stripped: str) -> bool:
    """
    Check if an escaped line starting with &gt; is a face like >_< rather than a quote.

    A face is &gt;, up to three characters, then &lt;. An escaped entity counts
    as one character. A raw < (from rendered spoiler or quote HTML) or a
    placeholder can't be part of a face.
    """
    return _FACE_LINE.match(stripped) is not None


def _sub_links(replace: Callable[[re.Match[str]], str], text: str) -> str:
    """
    Same result as _LINK_PATTERN.sub(replace, text), in linear time.

    re.sub tries the pattern at every "[", and a failed attempt can scan to
    the end of the text, so many "[" without a "](...)" after them took
    quadratic time. A match at a "[" is fully determined by the first "]"
    after it and the first ")" after that: every "[" before the same "]"
    matches or fails together, so one check covers them all and the scan
    moves on past that "]".
    """
    parts = []
    done = 0  # text[:done] is already in parts
    position = 0  # where to look for the next "["
    while (start := text.find("[", position)) != -1:
        close = text.find("]", start + 1)
        if close == -1:
            break  # No "]" after any remaining "["
        if close == start + 1 or not text.startswith("(", close + 1):
            # Empty link text (start is the only "[" before this "]"), or no
            # "(" after the "]": no "[" before this "]" can match
            position = close + 1
            continue
        end = text.find(")", close + 2)
        if end == -1:
            break  # No ")" left, so no later link can close either
        if end == close + 2:
            position = close + 1  # Empty URL
            continue
        match = _LINK_PATTERN.match(text, start)
        assert match is not None  # the checks above are exactly the pattern
        parts += [text[done:start], replace(match)]
        done = position = end + 1
    parts.append(text[done:])
    return "".join(parts)


def escape_source(text: str) -> str:
    """
    Normalize stored HTML entities, then HTML-escape the raw text.
    """
    # First normalize any HTML entities that may have been stored in the DB
    # (e.g. &quot;, &amp;, etc.) by unescaping them, then escape to ensure a
    # single correct encoding. This prevents double-encoding like
    # '&amp;quot;' showing up in the rendered HTML.
    # Then escape all HTML entities in the raw text
    return escape(unescape(text))


def render_inline(text: str) -> str:
    """
    Render links, bare URLs, bold and italics on already-escaped text.
    """

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

        # Write * as a character reference so the bold/italic passes below
        # can't see it; the browser decodes the attribute back to the same URL.
        href = escape(url).replace("*", "&#42;")

        return f'<a href="{href}" rel="nofollow noopener" target="_blank">{link_text}</a>'

    text = _sub_links(replace_link, text)

    # Auto-link bare URLs (https://... or http://...)
    # Runs AFTER explicit markdown links to avoid double-linking.
    # To prevent nested <a> tags, we split text on existing anchor tags
    # and only auto-link within non-anchor segments.
    # Excludes * and _ so markdown bold/italic delimiters adjacent to URLs
    # aren't consumed into the match. The text is already escaped, so a quote
    # or angle bracket is the entity &quot; &lt; or &gt;, which also ends it.
    # A raw " only ever comes from HTML this module wrote, so excluding it
    # keeps a match from running out of an attribute.
    _bare_url_re = re.compile(r'https?://(?:(?!&quot;|&lt;|&gt;)[^\s<>\[\]*"])+')

    def replace_bare_url(match: re.Match[str]) -> str:
        escaped_url = match.group(0)

        # Work on unescaped URL for punctuation stripping and validation,
        # since the text is already HTML-escaped (& → &amp;) at this point.
        raw_url = unescape(escaped_url)

        # Strip trailing punctuation that's unlikely to be part of the URL
        trailing = ""
        while raw_url and raw_url[-1] in ".,!?;:'":
            trailing = raw_url[-1] + trailing
            raw_url = raw_url[:-1]

        # Handle unbalanced trailing parenthesis — strip ) only if no matching (
        while raw_url.endswith(")") and raw_url.count("(") < raw_url.count(")"):
            trailing = ")" + trailing
            raw_url = raw_url[:-1]

        # Defense-in-depth: the regex only matches http(s):// which is_safe_url
        # always accepts, but this guard protects against future regex changes.
        if not is_safe_url(raw_url):
            return match.group(0)

        # Re-escape the cleaned URL for safe use in href and display text.
        # The href stays as typed; only the visible text is decoded.
        safe_url_html = escape(raw_url, quote=True)
        display_html = escape(_decode_for_display(raw_url), quote=True)

        return f'<a href="{safe_url_html}" rel="nofollow noopener" target="_blank">{display_html}</a>{escape(trailing)}'

    # Split on <a ...>...</a> spans to avoid auto-linking inside existing anchors
    # DOTALL: an explicit link's text can span lines
    parts = re.split(r"(<a\s[^>]*>.*?</a>)", text, flags=re.DOTALL)
    for i, part in enumerate(parts):
        # Only process segments that are NOT existing anchor tags
        if not part.startswith("<a "):
            parts[i] = _bare_url_re.sub(replace_bare_url, part)
    text = "".join(parts)

    # Process bold **text**
    # Use negative lookbehind/lookahead to avoid matching single * meant for italic
    text = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", text)

    # Process italic *text*
    # Avoid matching ** (already processed) or * at start of word boundaries
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    return text
