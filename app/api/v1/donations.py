"""Donations API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.misc import Donations
from app.schemas.donations import DonationListResponse, DonationResponse

router = APIRouter(prefix="/donations", tags=["donations"])


@router.get("/", response_model=DonationListResponse, include_in_schema=False)
@router.get("", response_model=DonationListResponse)
async def list_donations(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DonationListResponse:
    """List recent donations, newest first."""
    query = select(Donations).order_by(desc(Donations.date)).limit(limit)
    result = await db.execute(query)
    rows = result.scalars().all()

    return DonationListResponse(donations=[DonationResponse.model_validate(row) for row in rows])
