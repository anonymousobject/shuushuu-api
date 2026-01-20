"""
API v1 Router
"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    comments,
    favorites,
    history,
    images,
    meta,
    permissions,
    privmsgs,
    tags,
    users,
)
from app.api.v1.tags import character_source_links_router

router = APIRouter()

# Include all endpoint routers
router.include_router(admin.router)
router.include_router(auth.router)
router.include_router(images.router)
router.include_router(tags.router)
router.include_router(character_source_links_router)
router.include_router(users.router)
router.include_router(history.router)
router.include_router(favorites.router)
router.include_router(comments.router)
router.include_router(privmsgs.router)
router.include_router(meta.router)
router.include_router(permissions.router)

__all__ = ["router"]
