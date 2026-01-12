"""
User loading utilities with standard eager loading options.

Use USER_WITH_GROUPS_OPTIONS when loading users that need groups populated.
This ensures UserSummary.model_validate(user) automatically includes groups.
"""

from sqlalchemy.orm import selectinload

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
