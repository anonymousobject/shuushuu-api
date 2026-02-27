"""Donations API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.misc import Donations
from app.schemas.donations import (
    DonationListResponse,
    DonationResponse,
    MonthlyDonationResponse,
    MonthlyDonationTotal,
)

router = APIRouter(prefix="/donations", tags=["donations"])


@router.get("/monthly", response_model=MonthlyDonationResponse)
async def monthly_donations(
    db: Annotated[AsyncSession, Depends(get_db)],
    months: Annotated[int, Query(ge=1, le=24)] = 6,
) -> MonthlyDonationResponse:
    """Get donation totals grouped by month."""
    cutoff = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff - relativedelta(months=months - 1)

    query = (
        select(
            extract("year", Donations.date).label("year"),
            extract("month", Donations.date).label("month"),
            func.sum(Donations.amount).label("total"),
        )
        .where(Donations.date >= cutoff)
        .group_by(
            extract("year", Donations.date),
            extract("month", Donations.date),
        )
        .order_by(
            extract("year", Donations.date).desc(),
            extract("month", Donations.date).desc(),
        )
    )
    result = await db.execute(query)
    rows = result.all()

    return MonthlyDonationResponse(
        monthly_totals=[
            MonthlyDonationTotal(year=int(row.year), month=int(row.month), total=int(row.total))
            for row in rows
        ]
    )


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
