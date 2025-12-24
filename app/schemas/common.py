"""
Shared/common Pydantic schemas used across multiple endpoints
"""

from pydantic import BaseModel, computed_field

from app.config import settings


class UserSummary(BaseModel):
    """
    Minimal user information for embedding in responses.

    Used across image, comment, and other endpoints to avoid N+1 queries
    when clients need basic user info without fetching the full user profile.
    """

    user_id: int
    username: str
    avatar: str | None = None  # Avatar filename from database

    # Allow Pydantic to read from SQLAlchemy model attributes (not just dicts)
    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        """Generate avatar URL from avatar field"""
        if self.avatar:
            return f"{settings.IMAGE_BASE_URL}/images/avatars/{self.avatar}"
        return None
