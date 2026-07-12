"""
User loading utilities with standard eager loading options.

Use USER_WITH_GROUPS_OPTIONS when loading users that need groups populated.
This ensures UserSummary.model_validate(user) automatically includes groups.
"""

from sqlalchemy.orm import selectinload
from sqlalchemy.orm.strategy_options import _AbstractLoad

from app.models.image import Images
from app.models.permissions import UserGroups
from app.models.user import Users

# Standard options for loading users with their groups
# Usage: select(Users).options(*USER_WITH_GROUPS_OPTIONS)
USER_WITH_GROUPS_OPTIONS = (
    selectinload(Users.user_groups).selectinload(UserGroups.group),  # type: ignore[arg-type]
)


def user_with_groups_options() -> tuple:  # type: ignore[type-arg]
    """
    Return SQLAlchemy options for loading a user with their groups.

    Usage for loading via relationship:
        selectinload(Images.user).options(*user_with_groups_options())

    Returns options that eager load user_groups and their associated groups,
    so the User.groups property works without additional queries.
    """
    return (
        selectinload(Users.user_groups).selectinload(UserGroups.group),  # type: ignore[arg-type]
    )


def image_uploader_load() -> _AbstractLoad:
    """
    Return the standard eager-load option for an image's uploader summary.

    Usage: select(Images).options(image_uploader_load())

    Loads only the columns UserSummary needs (id, username, avatar,
    avatar_in_r2, user_title), avoiding a full Users row fetch.
    """
    return selectinload(Images.user).load_only(  # type: ignore[arg-type]
        Users.user_id,  # type: ignore[arg-type]
        Users.username,  # type: ignore[arg-type]
        Users.avatar,  # type: ignore[arg-type]
        Users.avatar_in_r2,  # type: ignore[arg-type]
        Users.user_title,  # type: ignore[arg-type]
    )
