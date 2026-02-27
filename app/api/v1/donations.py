"""Donations API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.database import get_db
from app.core.permissions import Permission, has_permission
from app.core.redis import get_redis
from app.models.misc import Donations
from app.models.user import Users
from app.schemas.donations import (
    DonationCreate,
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
    now = datetime.now(UTC)
    # Subtract (months - 1) months using integer arithmetic to avoid dateutil dependency
    total = now.year * 12 + now.month - (months - 1)
    cutoff = datetime((total - 1) // 12, (total - 1) % 12 + 1, 1, tzinfo=UTC)

    query = (
        select(
            extract("year", Donations.date).label("year"),  # type: ignore[arg-type]
            extract("month", Donations.date).label("month"),  # type: ignore[arg-type]
            func.sum(Donations.amount).label("total"),
        )
        .where(Donations.date >= cutoff)  # type: ignore[arg-type]
        .group_by(
            extract("year", Donations.date),  # type: ignore[arg-type]
            extract("month", Donations.date),  # type: ignore[arg-type]
        )
        .order_by(
            extract("year", Donations.date).desc(),  # type: ignore[arg-type]
            extract("month", Donations.date).desc(),  # type: ignore[arg-type]
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
    query = (
        select(Donations, Users.username)  # type: ignore[call-overload]
        .outerjoin(Users, Donations.user_id == Users.user_id)
        .order_by(desc(Donations.date))  # type: ignore[arg-type]
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    return DonationListResponse(
        donations=[
            DonationResponse(
                date=donation.date,
                amount=donation.amount,
                nick=donation.nick,
                user_id=donation.user_id,
                username=username,
            )
            for donation, username in rows
        ]
    )


@router.post(
    "/",
    response_model=DonationResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post("", response_model=DonationResponse, status_code=status.HTTP_201_CREATED)
async def create_donation(
    body: DonationCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> DonationResponse:
    """Create a donation record. Requires DONATIONS_CREATE permission."""
    assert current_user.user_id is not None

    if not await has_permission(
        db, current_user.user_id, Permission.DONATIONS_CREATE, redis_client
    ):
        raise HTTPException(status_code=403, detail="DONATIONS_CREATE permission required")

    donation = Donations(
        amount=body.amount,
        nick=body.nick,
        user_id=body.user_id,
    )
    if body.date is not None:
        donation.date = body.date

    db.add(donation)
    await db.commit()
    await db.refresh(donation)

    return DonationResponse(
        date=donation.date,
        amount=donation.amount,  # type: ignore[arg-type]
        nick=donation.nick,
        user_id=donation.user_id,
    )
