"""
Meta/Configuration API endpoints
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/meta", tags=["meta"])


class PublicConfig(BaseModel):
    """Public configuration exposed to frontend"""

    max_search_tags: int
    max_image_size: int
    max_avatar_size: int
    upload_delay_seconds: int
    search_delay_seconds: int


@router.get("/config", response_model=PublicConfig)
async def get_public_config() -> PublicConfig:
    """
    Get public configuration settings.
    """
    return PublicConfig(
        max_search_tags=settings.MAX_SEARCH_TAGS,
        max_image_size=settings.MAX_IMAGE_SIZE,
        max_avatar_size=settings.MAX_AVATAR_SIZE,
        upload_delay_seconds=settings.UPLOAD_DELAY_SECONDS,
        search_delay_seconds=settings.SEARCH_DELAY_SECONDS,
    )
