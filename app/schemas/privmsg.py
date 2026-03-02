"""
Pydantic schemas for Privmsg endpoints
"""

from pydantic import BaseModel, computed_field, field_validator

from app.models.privmsg import PrivmsgBase
from app.utils.markdown import normalize_legacy_entities, parse_markdown


class PrivmsgCreate(BaseModel):
    """Schema for creating a new private message"""

    to_user_id: int
    subject: str
    message: str
    thread_id: str | None = None

    @field_validator("subject")
    @classmethod
    def sanitize_subject(cls, v: str) -> str:
        """
        Sanitize PM subject.

        Just trims whitespace - HTML escaping is handled by Svelte's
        safe template interpolation on the frontend.
        """
        return v.strip()

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        """
        Sanitize PM message text.

        Message body supports markdown, so we store raw user input and let
        parse_markdown() handle HTML escaping at render time.
        """
        return v.strip()


class PrivmsgMessage(PrivmsgBase):
    """Schema for retrieving private messages for a user"""

    privmsg_id: int
    viewed: int
    from_username: str | None = None
    to_username: str | None = None
    # Optional avatar URLs for display in clients
    from_avatar_url: str | None = None
    to_avatar_url: str | None = None
    # User groups for username coloring
    from_groups: list[str] = []
    to_groups: list[str] = []

    @field_validator("subject", "text", mode="before")
    @classmethod
    def normalize_db_fields(cls, v: str | None) -> str | None:
        """
        Normalize fields from database for legacy PHP data.

        Handles PMs created in the old PHP codebase which stored data
        as HTML-encoded entities. New PMs: subject is escaped, message is raw.
        """
        return normalize_legacy_entities(v)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text_html(self) -> str:
        """Rendered HTML from markdown text"""
        return parse_markdown(self.text or "")


class PrivmsgMessages(BaseModel):
    """Schema for paginated private message list"""

    total: int
    page: int
    per_page: int
    messages: list[PrivmsgMessage]


class ThreadSummary(BaseModel):
    """Schema for a conversation thread summary in the inbox."""

    thread_id: str
    subject: str
    other_user_id: int
    other_username: str | None = None
    other_avatar_url: str | None = None
    other_groups: list[str] = []
    latest_message_preview: str
    latest_message_date: str
    unread_count: int
    message_count: int


class ThreadList(BaseModel):
    """Schema for paginated thread list."""

    total: int
    page: int
    per_page: int
    threads: list[ThreadSummary]
