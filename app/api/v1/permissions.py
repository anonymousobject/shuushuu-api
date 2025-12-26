"""
Public permissions API - list available permission names.

This module provides a public endpoint for retrieving the list of
available permissions. This information is used by the frontend
for permission-based UI rendering and validation.
"""

from fastapi import APIRouter

from app.core.permissions import Permission

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("")
async def list_permission_names() -> dict[str, str]:
    """
    List all available permission names.

    Returns a mapping of permission names (enum names) to their values
    (database titles). This is public information used by the frontend
    for permission checking.

    Returns:
        Dictionary mapping permission names to values
        Example: {"IMAGE_TAG_ADD": "image_tag_add", "TAG_CREATE": "tag_create"}
    """
    return {perm.name: perm.value for perm in Permission}
