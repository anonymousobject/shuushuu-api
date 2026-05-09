"""
Shared/common Pydantic schemas used across multiple endpoints
"""

from pydantic import BaseModel, Field, computed_field


class UserSummary(BaseModel):
    """
    Minimal user information for embedding in responses.

    Used across image, comment, and other endpoints to avoid N+1 queries
    when clients need basic user info without fetching the full user profile.
    """

    user_id: int
    username: str
    avatar: str | None = None  # Avatar filename from database
    # Internal storage-routing detail consumed by the avatar_url
    # computed_field; exclude=True keeps it out of the API response while
    # still letting the property read it.
    avatar_in_r2: bool = Field(default=False, exclude=True)
    user_title: str | None = None
    groups: list[str] = []  # Group names for username coloring (e.g., ["mods", "admins"])

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        from app.services.avatar import avatar_url as _build_avatar_url

        return _build_avatar_url(self.avatar, self.avatar_in_r2)
