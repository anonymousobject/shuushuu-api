"""
Comment query services shared by the comments and images endpoints.
"""

from sqlalchemy.orm import selectinload
from sqlalchemy.orm.strategy_options import _AbstractLoad

from app.models import Comments, Users
from app.models.permissions import UserGroups


def comment_user_eager_load() -> _AbstractLoad:
    """Eager-load chain for a comment's author (user -> user_groups -> group)."""
    return (
        selectinload(Comments.user)  # type: ignore[arg-type]
        .selectinload(Users.user_groups)  # type: ignore[arg-type]
        .selectinload(UserGroups.group)  # type: ignore[arg-type]
    )
