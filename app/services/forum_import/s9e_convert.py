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


def _inner(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        parts.append(_render(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _render(el: ET.Element) -> str:
    tag = el.tag
    if tag in _DROP_TAGS:
        return ""
    if tag == "br":
        return "\n"
    if tag == "IMG":
        return f"[image]({el.get('src', '')})"
    if tag == "ATTACHMENT":
        return ""
    inner = _inner(el)
    if tag in ("r", "t", "E", "COLOR", "SIZE", "FONT", "U", "CODE", "LIST"):
        return inner
    if tag == "B":
        return f"**{inner}**"
    if tag == "I":
        return f"*{inner}*"
    if tag == "QUOTE":
        author = el.get("author")
        return f'[quote="{author}"]{inner}[/quote]' if author else f"[quote]{inner}[/quote]"
    if tag == "URL":
        url = el.get("url", "")
        return f"[{inner or url}]({url})"
    if tag == "LI":
        return f"\n- {inner.strip()}"
    # Unknown element: keep its inner text (nothing silently dropped).
    return inner


def _normalize(text: str) -> str:
    # Collapse 3+ blank lines the list/quote handling can introduce.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
