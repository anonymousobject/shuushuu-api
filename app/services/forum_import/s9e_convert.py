"""Convert phpBB s9e-TextFormatter XML (`post_text`) to the site's markdown subset.

phpBB 3.2+ stores each post as an XML tree: root `<t>` (plain) or `<r>` (rich),
formatted spans as uppercased BBCode-named elements (`<B>`, `<QUOTE author=...>`,
`<URL url=...>`, ...), and `<s>`/`<e>`/`<i>` markers holding the original BBCode
source (which we drop). The mapping targets exactly what parse_markdown renders:
bold/italic, `[quote]`, `[text](url)`. Lossy tags (color/size/font/underline) keep
their inner text. Inline `<ATTACHMENT>` is dropped — the importer appends a
canonical attachment-link list instead.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

# Elements whose entire subtree is the original BBCode source markup: drop them.
_DROP_TAGS = {"s", "e", "i"}


def s9e_to_markdown(xml: str) -> str:
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        # Defensive: not valid s9e XML — strip any tags and return the text.
        return re.sub(r"<[^>]+>", "", xml).strip()
    return _normalize(_render(root))


# s9e keeps the user's own newline characters as text nodes *and* adds markup for them:
# every <br/> is followed by a literal "\n", and list markup sits on lines of its own.
# Rendering both would double each break, so the markup defers to the text.
_LINE_START_TAGS = {"LIST", "LI"}


def _inner(el: ET.Element) -> str:
    text = el.text or ""
    for child in el:
        if child.tag in _LINE_START_TAGS and text and not text.endswith("\n"):
            text += "\n"
        text += _render(child)
        tail = child.tail or ""
        if child.tag == "br" and tail.startswith("\n"):
            tail = tail[1:]
        text += tail
    return text


def _quote(el: ET.Element, inner: str) -> str:
    author = el.get("author")
    return f'[quote="{author}"]{inner}[/quote]' if author else f"[quote]{inner}[/quote]"


def _url(el: ET.Element, inner: str) -> str:
    url = el.get("url", "")
    return f"[{inner or url}]({url})"


# Per-tag formatters taking (element, rendered inner text).
_FORMATTERS: dict[str, Callable[[ET.Element, str], str]] = {
    "br": lambda _el, _inner: "\n",
    "IMG": lambda el, _inner: f"[image]({el.get('src', '')})",
    "ATTACHMENT": lambda _el, _inner: "",
    "B": lambda _el, inner: f"**{inner}**",
    "I": lambda _el, inner: f"*{inner}*",
    "QUOTE": _quote,
    "URL": _url,
    "LIST": lambda _el, inner: inner.strip("\n"),
    "LI": lambda _el, inner: f"- {inner.strip()}",
}


def _render(el: ET.Element) -> str:
    if el.tag in _DROP_TAGS:
        return ""
    formatter = _FORMATTERS.get(el.tag)
    # Passthrough and unknown elements keep their inner text (nothing silently dropped).
    return formatter(el, _inner(el)) if formatter else _inner(el)


def _normalize(text: str) -> str:
    # Collapse 3+ blank lines the list/quote handling can introduce.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
