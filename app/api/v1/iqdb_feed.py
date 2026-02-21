"""
XML feed endpoint for iqdb.org crawling.

Provides a public XML API at /image/index.xml matching the old PHP format,
so iqdb.org can discover and index e-shuushuu images.
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.config import ImageStatus, TagType, settings
from app.core.database import get_db
from app.models.image import Images
from app.models.tag import Tags
from app.models.tag_link import TagLinks

router = APIRouter(tags=["iqdb"])

# Map tag type integers to XML attribute names
TAG_TYPE_ATTRS = {
    TagType.THEME: "theme_tags",
    TagType.ARTIST: "artist",
    TagType.SOURCE: "source",
    TagType.CHARACTER: "characters",
}


@router.get("/image/index.xml")
async def iqdb_feed(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 16,
    after_id: Annotated[int | None, Query()] = None,
) -> Response:
    """XML feed of images for iqdb.org crawling."""
    query = select(Images).where(Images.status == ImageStatus.ACTIVE)

    if after_id is not None:
        query = query.where(col(Images.image_id) > after_id).order_by(col(Images.image_id).asc())
    else:
        query = query.order_by(col(Images.image_id).desc())

    query = query.limit(limit)

    result = await db.execute(query)
    images = result.scalars().all()

    if not images:
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n<images />'
        return Response(content=xml_str, media_type="application/xml; charset=utf-8")

    # Batch-load tags for all images
    image_ids = [img.image_id for img in images]
    tag_query = (
        select(TagLinks.image_id, Tags.title, Tags.type)
        .join(Tags, col(TagLinks.tag_id) == col(Tags.tag_id))
        .where(col(TagLinks.image_id).in_(image_ids))
    )
    tag_result = await db.execute(tag_query)
    tag_rows = tag_result.all()

    # Group tags by image_id and type
    # {image_id: {tag_type: [title, ...]}}
    image_tags: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for image_id, title, tag_type in tag_rows:
        image_tags[image_id][tag_type].append(title)

    # Build XML
    root = ET.Element("images")
    for image in images:
        attrs: dict[str, str] = {
            "id": str(image.image_id),
            "md5": image.md5_hash,
            "status": ImageStatus.get_label(image.status),
            "submitted_on": image.date_added.isoformat() if image.date_added else "",
            "submitted_by": str(image.user_id),
            "width": str(image.width),
            "height": str(image.height),
            "filesize": str(image.filesize),
            "preview_url": f"{settings.IMAGE_BASE_URL}/thumbs/{image.filename}.webp",
        }

        # Add tag attributes
        tags_for_image = image_tags.get(image.image_id or 0, {})
        for tag_type, attr_name in TAG_TYPE_ATTRS.items():
            titles = tags_for_image.get(tag_type, [])
            if titles:
                attrs[attr_name] = ", ".join(titles)

        ET.SubElement(root, "image", attrib=attrs)

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    return Response(content=xml_str, media_type="application/xml; charset=utf-8")
