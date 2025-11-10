"""
Pydantic schemas for Privmsg endpoints
"""

from pydantic import BaseModel

from app.models.privmsg import PrivmsgBase


class PrivmsgCreate(PrivmsgBase):
    """Schema for creating a new private message"""

    pass


class PrivmsgMessage(PrivmsgBase):
    """Schema for retrieving private messages for a user"""

    viewed: int


class PrivmsgMessages(BaseModel):
    """Schema for paginated private message list"""

    total: int
    page: int
    per_page: int
    messages: list[PrivmsgMessage]
