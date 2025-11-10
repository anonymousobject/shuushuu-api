"""
Privmsgs API endpoints
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Privmsgs
from app.schemas.privmsg import PrivmsgMessage, PrivmsgMessages

router = APIRouter(prefix="/privmsgs", tags=["privmsgs"])


@router.get("/", response_model=PrivmsgMessages)
async def get_user_privmsgs(
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, le=100),
    to_user_id: int | None = Query(None, description="Filter by recipient user ID"),
    from_user_id: int | None = Query(None, description="Filter by sender user ID"),
) -> PrivmsgMessages:
    """
    Retrieve private messages for a user with pagination.
    """
    query = select(Privmsgs)

    if to_user_id is not None:
        query = query.where(Privmsgs.to_user_id == to_user_id)  # type: ignore[arg-type]
    if from_user_id is not None:
        query = query.where(Privmsgs.from_user_id == from_user_id)  # type: ignore[arg-type]

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply sorting
    sort_column = Privmsgs.date
    query = query.order_by(desc(sort_column))  # type: ignore[arg-type]

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    messages = result.scalars().all()

    return PrivmsgMessages(
        total=total or 0,
        page=page,
        per_page=per_page,
        messages=[PrivmsgMessage.model_validate(msg) for msg in messages],
    )
