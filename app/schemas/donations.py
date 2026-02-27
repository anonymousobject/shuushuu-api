"""Pydantic schemas for Donations endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import UTCDatetime


class DonationCreate(BaseModel):
    """Schema for creating a donation record."""

    amount: int = Field(gt=0, description="Donation amount")
    nick: str | None = Field(default=None, max_length=30, description="Donor display name")
    user_id: int | None = Field(default=None, description="Donor user ID")
    date: datetime | None = Field(default=None, description="Donation date (defaults to now)")

    @field_validator("nick", mode="before")
    @classmethod
    def strip_nick(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            return v.strip()
        return v


class DonationResponse(BaseModel):
    """Schema for a single donation in API responses."""

    date: UTCDatetime
    amount: int
    nick: str | None
    user_id: int | None
    username: str | None = None


class DonationListResponse(BaseModel):
    """Schema for recent donations list."""

    donations: list[DonationResponse]


class MonthlyDonationTotal(BaseModel):
    """A single month's donation total."""

    year: int
    month: int
    total: int


class MonthlyDonationResponse(BaseModel):
    """Schema for monthly donation totals."""

    monthly_totals: list[MonthlyDonationTotal]
