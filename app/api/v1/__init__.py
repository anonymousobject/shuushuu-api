"""
API v1 Router
"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    banners,
    comments,
    donations,
    favorites,
    feeds,
    history,
    images,
    meta,
    ml_analyze,
    ml_suggestion_queue,
    ml_tag_suggestions,
    news,
    permissions,
    privmsgs,
    search,
    tags,
    url_import,
    users,
)
from app.api.v1.tags import character_source_links_router

router = APIRouter()

# Include all endpoint routers
router.include_router(admin.router)
router.include_router(auth.router)
router.include_router(banners.router)
router.include_router(donations.router)
router.include_router(feeds.router)
# url_import must precede images: its literal /images/* paths would otherwise
# be captured by the /images/{image_id} int path param and 422.
router.include_router(url_import.router)
router.include_router(images.router)
router.include_router(ml_analyze.router)
router.include_router(ml_suggestion_queue.router)
router.include_router(ml_tag_suggestions.router)
router.include_router(tags.router)
router.include_router(character_source_links_router)
router.include_router(users.router)
router.include_router(history.router)
router.include_router(favorites.router)
router.include_router(comments.router)
router.include_router(privmsgs.router)
router.include_router(search.router)
router.include_router(meta.router)
router.include_router(news.router)
router.include_router(permissions.router)

__all__ = ["router"]
