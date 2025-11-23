"""
Pydantic schemas for Privmsg endpoints
"""

from pydantic import BaseModel, computed_field

from app.models.privmsg import PrivmsgBase
from app.utils.markdown import parse_markdown


class PrivmsgCreate(BaseModel):
    """Schema for creating a new private message"""

    to_user_id: int
    subject: str
    message: str


class PrivmsgMessage(PrivmsgBase):
    """Schema for retrieving private messages for a user"""

    viewed: int

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
