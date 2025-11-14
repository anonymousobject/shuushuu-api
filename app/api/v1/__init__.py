"""
API v1 Router
"""

from fastapi import APIRouter

from app.api.v1 import auth, comments, favorites, images, privmsgs, tags, users

router = APIRouter()

# Include all endpoint routers
router.include_router(auth.router)
router.include_router(images.router)
router.include_router(tags.router)
router.include_router(users.router)
router.include_router(favorites.router)
router.include_router(comments.router)
router.include_router(privmsgs.router)

__all__ = ["router"]
