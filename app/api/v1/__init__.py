"""
API v1 Router
"""

from fastapi import APIRouter

from app.api.v1 import admin, auth, comments, favorites, images, privmsgs, tag_suggestions, tags, users

router = APIRouter()

# Include all endpoint routers
router.include_router(admin.router)
router.include_router(auth.router)
router.include_router(images.router)
router.include_router(tags.router)
router.include_router(users.router)
router.include_router(favorites.router)
router.include_router(comments.router)
router.include_router(privmsgs.router)
router.include_router(tag_suggestions.router)

__all__ = ["router"]
