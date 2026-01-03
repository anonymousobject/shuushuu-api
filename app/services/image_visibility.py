"""
Image visibility service.

Determines whether a user can view an image file based on:
- Image status (public vs protected)
- User ownership (owners can view their own images)
- User permissions (moderators can view all images)

Note: This controls FILE access only. API metadata endpoints remain unrestricted.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus
from app.core.permissions import Permission, has_any_permission
from app.models.image import Images
from app.models.user import Users

# Public statuses that anyone can view
# REPOST (-1), ACTIVE (1), SPOILER (2)
PUBLIC_IMAGE_STATUSES: frozenset[int] = frozenset(
    {
        ImageStatus.REPOST,
        ImageStatus.ACTIVE,
        ImageStatus.SPOILER,
    }
)


async def can_view_image_file(
    image: Images,
    user: Users | None,
    db: AsyncSession,
) -> bool:
    """
    Check if a user can view an image file.

    Visibility rules:
    - Public statuses (-1, 1, 2): Anyone can view
    - Owner: Can view their own images regardless of status
    - Moderators: Users with IMAGE_EDIT or REVIEW_VIEW can view all

    Args:
        image: The image to check visibility for
        user: The requesting user (None for anonymous)
        db: Database session for permission lookups

    Returns:
        True if user can view the image file, False otherwise

    Note:
        Future enhancement: Support ?token=xxx query param for non-browser clients.
        See design doc for details.
    """
    # Public statuses are visible to all
    if image.status in PUBLIC_IMAGE_STATUSES:
        return True

    # Anonymous users can only see public statuses
    if user is None:
        return False

    # Owner can view their own images
    if image.user_id == user.user_id:
        return True

    # Moderators can view all - check for IMAGE_EDIT or REVIEW_VIEW
    assert user.user_id is not None
    return await has_any_permission(
        db,
        user.user_id,
        [Permission.IMAGE_EDIT, Permission.REVIEW_VIEW],
    )
