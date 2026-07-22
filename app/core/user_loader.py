"""
User loading utilities with standard eager-loading options.

USER_WITH_GROUPS_OPTIONS eager-loads a user's groups so
UserSummary.model_validate(user) includes them — splat it into a query
selecting Users directly. image_uploader_load() composes it with a
column-restricted load for the common case of an image's uploader summary.
"""

from sqlalchemy.orm import Load, load_only, selectinload

from app.models.image import Images
from app.models.permissions import UserGroups
from app.models.user import Users

# Canonical eager-load for a user's groups, so the User.groups property (and
# UserSummary.groups) resolve without a lazy load. Single source of truth for
# the user_groups→group chain; reuse it rather than re-inlining it.
# Usage: select(Users).options(*USER_WITH_GROUPS_OPTIONS)
USER_WITH_GROUPS_OPTIONS = (
    selectinload(Users.user_groups).selectinload(UserGroups.group),  # type: ignore[arg-type]
)


def image_uploader_load() -> Load:
    """
    Return the standard eager-load option for an image's uploader summary.

    Usage: select(Images).options(image_uploader_load())

    Loads only the columns UserSummary needs (id, username, avatar,
    avatar_in_r2, user_title), avoiding a full Users row fetch, PLUS the
    user_groups→group relationship so UserSummary.groups is populated
    (without it, User.groups returns [] and the frontend can't colour
    admin/mod/tagger usernames — e.g. the /ml-suggestions hover popup).
    """
    return selectinload(Images.user).options(  # type: ignore[arg-type, return-value]
        load_only(
            Users.user_id,  # type: ignore[arg-type]
            Users.username,  # type: ignore[arg-type]
            Users.avatar,  # type: ignore[arg-type]
            Users.avatar_in_r2,  # type: ignore[arg-type]
            Users.user_title,  # type: ignore[arg-type]
        ),
        *USER_WITH_GROUPS_OPTIONS,
    )
