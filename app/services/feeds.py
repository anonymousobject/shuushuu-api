"""Atom feed rendering and query helpers."""

from typing import Any

from app.config import TagType

_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def mime_type_for_ext(ext: str) -> str:
    """Map a file extension (case-insensitive, no leading dot) to its MIME type.

    Returns 'application/octet-stream' for unknown or empty extensions — Atom
    validators accept this and feed readers handle it gracefully.
    """
    return _MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def _pick_representative(tags: list[Any], type_value: int) -> str | None:
    """Return the title of the highest-usage tag of the given type, or None."""
    candidates = [t for t in tags if t.type_id == type_value]
    if not candidates:
        return None
    # Stable sort: usage_count DESC, then tag_id ASC for determinism on ties.
    candidates.sort(key=lambda t: (-t.usage_count, t.tag_id))
    return candidates[0].tag


def compose_entry_title(image_id: int, tags: list[Any]) -> str:
    """Build the Atom entry <title> per the design spec.

    Format: '{characters} ({sources}) by {artists}' — single representative tag
    per category (highest usage_count). Empty sections are skipped. Falls back
    to 'Image #{image_id}' if no character, source, or artist tags are present.

    `tags` is a sequence of TagSummary-shaped objects with `.tag` (title),
    `.type_id` (int, matching TagType constants), `.tag_id`, and `.usage_count`.
    """
    char = _pick_representative(tags, TagType.CHARACTER)
    src = _pick_representative(tags, TagType.SOURCE)
    artist = _pick_representative(tags, TagType.ARTIST)

    parts: list[str] = []
    if char:
        parts.append(char)
    if src:
        parts.append(f"({src})")
    if artist:
        parts.append(f"by {artist}")

    if not parts:
        return f"Image #{image_id}"
    return " ".join(parts)
