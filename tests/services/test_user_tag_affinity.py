import pytest
from sqlalchemy import text

from app.models.user_tag_affinity import UserTagAffinity

pytestmark = [pytest.mark.integration, pytest.mark.needs_commit]


async def test_table_roundtrip(db_session):
    db_session.add(
        UserTagAffinity(
            user_id=1,
            tag_id=2,
            pool_cnt=10,
            fav_count=8,
            upload_count=3,
            rated_count=6,
            rating_avg=8.5,
            lift=4.2,
            rating_delta=1.5,
            affinity=2.19,
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT pool_cnt, affinity FROM user_tag_affinity WHERE user_id=1 AND tag_id=2")
        )
    ).one()
    assert row.pool_cnt == 10
    assert row.affinity == pytest.approx(2.19)


async def test_updated_at_server_default(db_session):
    # The refresh job inserts via raw INSERT…SELECT that OMITS updated_at,
    # relying on the server default. ORM inserts send explicit NULL (SQLModel
    # materializes the None default), so test the raw path directly.
    await db_session.execute(
        text(
            "INSERT INTO user_tag_affinity "
            "(user_id, tag_id, pool_cnt, fav_count, upload_count, rated_count, affinity) "
            "VALUES (7, 8, 5, 5, 0, 0, 1.0)"
        )
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT updated_at FROM user_tag_affinity WHERE user_id=7 AND tag_id=8")
        )
    ).one()
    assert row.updated_at is not None  # server_default filled it
