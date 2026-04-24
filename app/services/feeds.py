"""Atom feed rendering and query helpers."""

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
