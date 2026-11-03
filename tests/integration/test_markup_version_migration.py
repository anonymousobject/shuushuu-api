"""markup_version: existing rows are version 1, every later insert is version 2.

Runs the real Alembic chain in a scratch database (see test_schema_sync.py):
rows written before 0008 must come out as version 1, rows written after as 2.
"""

import os
import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.comment import Comments
from tests.conftest import TEST_DATABASE_URL
from tests.integration.test_schema_sync import _fresh_database

pytestmark = [
    pytest.mark.integration,
    # Fixed-name database: keep on one xdist worker (--dist loadgroup).
    pytest.mark.xdist_group("pg_markup_version_migration"),
]

_DB = "shuushuu_markup_version_pg"
_PREVIOUS_REVISION = "0007_forum_import_provenance"

_INSERTS = {
    "posts": "INSERT INTO posts (user_id, post_text, deleted, update_count, ip) VALUES (1, 'x', false, 0, '')",
    "forum_posts": "INSERT INTO forum_posts (thread_id, user_id, post_text, deleted, update_count, ip) VALUES (1, 1, 'x', false, 0, '')",
    "privmsgs": (
        "INSERT INTO privmsgs (from_user_id, to_user_id, subject, text, viewed, from_del, to_del, thread_id) "
        "VALUES (1, 1, 's', 'x', 0, 0, 0, 't')"
    ),
}


def _alembic_upgrade(url: str, revision: str) -> None:
    # Subprocess, not programmatic: the async env.py runs its own event loop.
    env = os.environ.copy()
    env["ALEMBIC_DB_URL"] = url
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", revision],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"alembic upgrade {revision} failed:\n{result.stderr}"


async def test_existing_rows_become_version_1_and_new_rows_version_2():
    await _fresh_database(_DB)
    url = make_url(TEST_DATABASE_URL).set(database=_DB)
    url_str = url.render_as_string(hide_password=False)

    _alembic_upgrade(url_str, _PREVIOUS_REVISION)

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # No parent rows needed: skip FK enforcement for these inserts.
            await conn.execute(text("SET session_replication_role = replica"))
            for sql in _INSERTS.values():
                await conn.execute(text(sql))

        _alembic_upgrade(url_str, "head")

        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role = replica"))
            for table in _INSERTS:
                versions = (
                    (await conn.execute(text(f"SELECT markup_version FROM {table}")))
                    .scalars()
                    .all()
                )
                assert versions == [1], f"{table} existing row"
            for table, sql in _INSERTS.items():
                await conn.execute(text(sql))
                versions = (
                    (await conn.execute(text(f"SELECT markup_version FROM {table} ORDER BY 1")))
                    .scalars()
                    .all()
                )
                assert versions == [1, 2], f"{table} new row"
    finally:
        await engine.dispose()


async def test_orm_insert_defaults_to_version_2(db_session: AsyncSession, test_user):
    comment = Comments(user_id=test_user.user_id, post_text="hello", date=datetime.now(UTC))
    db_session.add(comment)
    await db_session.commit()
    await db_session.refresh(comment)
    assert comment.markup_version == 2
