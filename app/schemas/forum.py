"""
Pydantic schemas for Forum endpoints
"""

from pydantic import BaseModel, Field, computed_field, field_validator

from app.core.permissions import FORUM_ACCESS_PERMISSIONS
from app.schemas.base import UTCDatetime, UTCDatetimeOptional
from app.schemas.common import UserSummary
from app.utils.markdown import parse_markdown


def _validate_access_perm(v: str | None) -> str | None:
    """Restrict category gate columns to the FORUM_ACCESS_* tier permissions."""
    if v is not None and v not in FORUM_ACCESS_PERMISSIONS:
        raise ValueError(f"must be one of: {', '.join(sorted(FORUM_ACCESS_PERMISSIONS))} (or null)")
    return v


# ===== Categories =====


class ForumCategoryCreate(BaseModel):
    """Schema for creating a category (FORUM_CATEGORY_MANAGE)."""

    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None

    _check_perms = field_validator("view_perm", "thread_create_perm", "reply_perm")(
        _validate_access_perm
    )

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: object) -> object:
        # mode="before" so whitespace-only input fails min_length after stripping
        return v.strip() if isinstance(v, str) else v


class ForumCategoryUpdate(BaseModel):
    """Schema for updating a category; only provided fields change (exclude_unset)."""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None

    _check_perms = field_validator("view_perm", "thread_create_perm", "reply_perm")(
        _validate_access_perm
    )

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ForumCategoryResponse(BaseModel):
    """Category with stats and caller capabilities."""

    category_id: int
    title: str
    description: str | None = None
    sort_order: int
    view_perm: str | None = None
    thread_create_perm: str | None = None
    reply_perm: str | None = None
    thread_count: int = 0
    post_count: int = 0
    last_post_at: UTCDatetimeOptional = None
    last_thread_id: int | None = None
    last_thread_title: str | None = None
    last_post_user: UserSummary | None = None
    can_create_thread: bool = False
    can_reply: bool = False


class ForumCategoryListResponse(BaseModel):
    categories: list[ForumCategoryResponse]


# ===== Threads =====


class ForumThreadCreate(BaseModel):
    """Create a thread with its opening post in one call."""

    title: str = Field(min_length=1, max_length=255)
    post_text: str = Field(min_length=1)

    @field_validator("title", "post_text", mode="before")
    @classmethod
    def strip_text(cls, v: object) -> object:
        # mode="before" so whitespace-only input fails min_length after stripping
        return v.strip() if isinstance(v, str) else v


class ForumThreadUpdate(BaseModel):
    """Partial thread update. title: author or FORUM_MODERATE;
    pinned/locked/category_id/deleted: FORUM_MODERATE only."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    pinned: bool | None = None
    locked: bool | None = None
    category_id: int | None = None
    deleted: bool | None = None

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ForumThreadSummary(BaseModel):
    """Thread row for lists and as the meta block of the detail view."""

    thread_id: int
    category_id: int
    title: str
    user: UserSummary
    date: UTCDatetime
    pinned: bool
    locked: bool
    deleted: bool
    post_count: int
    last_post_at: UTCDatetimeOptional = None
    last_post_user: UserSummary | None = None
    unread: bool = False


class ForumThreadListResponse(BaseModel):
    total: int
    page: int
    per_page: int
    threads: list[ForumThreadSummary]


# ===== Posts =====


class ForumPostCreate(BaseModel):
    post_text: str = Field(min_length=1, description="Post text (markdown supported)")

    @field_validator("post_text", mode="before")
    @classmethod
    def strip_text(cls, v: object) -> object:
        # mode="before" so whitespace-only input fails min_length after stripping
        return v.strip() if isinstance(v, str) else v


class ForumPostUpdate(BaseModel):
    """post_text: owner or FORUM_MODERATE; deleted: FORUM_MODERATE only."""

    post_text: str | None = Field(default=None, min_length=1)
    deleted: bool | None = None

    @field_validator("post_text", mode="before")
    @classmethod
    def strip_text(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ForumPostResponse(BaseModel):
    """Post as returned by the API. Tombstoned posts (deleted=True) have
    post_text blanked by the route for callers without FORUM_MODERATE."""

    post_id: int
    thread_id: int
    user_id: int
    post_text: str
    date: UTCDatetime
    deleted: bool
    update_count: int
    last_updated: UTCDatetimeOptional = None
    last_updated_user_id: int | None = None
    user: UserSummary

    @computed_field  # type: ignore[prop-decorator]
    @property
    def post_text_html(self) -> str:
        """Rendered HTML from markdown post_text"""
        return parse_markdown(self.post_text)


class ForumThreadDetailResponse(BaseModel):
    """Thread meta + one page of posts."""

    thread: ForumThreadSummary
    can_reply: bool = False
    can_moderate: bool = False
    total: int
    page: int
    per_page: int
    posts: list[ForumPostResponse]
