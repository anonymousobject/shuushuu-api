"""History rows for edits to an image's free-text metadata (miscmeta, source_url)."""

from typing import Any

from app.config import ImageMetadataField
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory


def build_metadata_history(
    image_id: int, current: Images, update_fields: dict[str, Any], user_id: int
) -> list[ImageMetadataHistory]:
    """One history row per tracked field whose value the update changes.

    Call before applying the update: old values are read from `current`.
    `update_fields` holds validated ImageUpdate values (already trimmed, blank
    as None). A legacy '', or a legacy value that's only whitespace or padded
    with it, reads the same as the update's own trimmed/blank-as-None value,
    so saving over it with its trimmed self records nothing. Untracked fields
    (caption) and unchanged values produce no row.
    """
    rows = []
    for field in ImageMetadataField.ALL:
        if field not in update_fields:
            continue
        old_value = (getattr(current, field) or "").strip() or None
        new_value = update_fields[field]
        if old_value != new_value:
            rows.append(
                ImageMetadataHistory(
                    image_id=image_id,
                    user_id=user_id,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                )
            )
    return rows
