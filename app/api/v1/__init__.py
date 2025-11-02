"""
API v1 Router
"""
from fastapi import APIRouter
from app.api.v1 import images, tags, users

router = APIRouter()

# Include all endpoint routers
router.include_router(images.router)
router.include_router(tags.router)
router.include_router(users.router)

__all__ = ["router"]
