"""Pydantic schemas for News endpoints."""

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.news import NewsBase
from app.schemas.base import UTCDatetime, UTCDatetimeOptional


class NewsCreate(BaseModel):
    """Schema for creating a news item."""

    title: str = Field(max_length=128, description="News title")
    news_text: str = Field(min_length=1, description="News content (plain text)")

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("news_text", mode="before")
    @classmethod
    def strip_news_text(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class NewsUpdate(BaseModel):
    """Schema for updating a news item. At least one field must be provided."""

    title: str | None = Field(default=None, max_length=128, description="News title")
    news_text: str | None = Field(default=None, description="News content (plain text)")

    @model_validator(mode="after")
    def at_least_one_field(self) -> NewsUpdate:
        if self.title is None and self.news_text is None:
            raise ValueError("At least one of title or news_text must be provided")
        return self

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("news_text", mode="before")
    @classmethod
    def strip_news_text(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            return v.strip()
        return v


class NewsResponse(NewsBase):
    """Schema for news response -- what the API returns."""

    news_id: int
    user_id: int
    username: str  # From users table join
    date: UTCDatetime
    edited: UTCDatetimeOptional = None

    model_config = {"from_attributes": True}


class NewsListResponse(BaseModel):
    """Schema for paginated news list."""

    total: int
    page: int
    per_page: int
    news: list[NewsResponse]
