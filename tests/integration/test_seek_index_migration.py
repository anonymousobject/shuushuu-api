"""0009 builds its five indexes CONCURRENTLY, and must not trust an INVALID leftover.

A failed or cancelled CREATE INDEX CONCURRENTLY leaves an INVALID index under its
name; IF NOT EXISTS alone would skip it on the re-run and record 0009 with an index
the planner never uses. Runs the real Alembic chain in a scratch database (see
test_schema_sync.py).
"""

import os
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import TEST_DATABASE_URL
from tests.integration.test_markup_version_migration import _alembic_upgrade
from tests.integration.test_schema_sync import _fresh_database

pytestmark = [
    pytest.mark.integration,
    # Fixed-name database: keep on one xdist worker (--dist loadgroup).
    pytest.mark.xdist_group("pg_seek_index_migration"),
]

_DB = "shuushuu_seek_index_migration_pg"
_PREVIOUS_REVISION = "0008_markup_version"
_INDEXES = {
    "idx_last_post_image_id": "(last_post, image_id)",
    "idx_bayesian_rating_image_id": "(bayesian_rating, image_id)",
    "idx_bayesian_rating_image_id_desc": "(bayesian_rating, image_id DESC)",
    "idx_favorites_image_id": "(favorites, image_id)",
    "idx_favorites_image_id_desc": "(favorites, image_id DESC)",
}
# Single-column indexes each composite makes redundant, which 0009 drops.
_REPLACED = {
    "idx_last_post": "(last_post)",
    "idx_bayesian_rating": "(bayesian_rating)",
    "idx_favorites": "(favorites)",
}
# Two images with the same favorites, so a UNIQUE build on favorites fails.
_INSERT_IMAGE = (
    "INSERT INTO images (filename, ext, md5_hash, filesize, width, height, caption, status,"
    " rating, user_id, locked, posts, favorites, bayesian_rating, num_ratings, ip, medium,"
    " large, has_theme, has_source, has_artist, has_character, r2_location)"
    " VALUES (:name, 'jpg', :name, 1, 1, 1, '', 1, 0, 1, 0, 0, 0, 0, 0, '', 0, 0,"
    " false, false, false, false, 0)"
)


async def _database_at_previous_revision() -> tuple[str, AsyncEngine]:
    await _fresh_database(_DB)
    url = make_url(TEST_DATABASE_URL).set(database=_DB)
    _alembic_upgrade(url.render_as_string(hide_password=False), _PREVIOUS_REVISION)
    # AUTOCOMMIT: CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    return url.render_as_string(hide_password=False), create_async_engine(
        url, isolation_level="AUTOCOMMIT"
    )


async def _indexes(engine: AsyncEngine) -> dict[str, tuple[bool, str]]:
    """{name: (indisvalid, indexdef)} for the indexes on images that 0009 builds or drops."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, ix.indisvalid, pg_get_indexdef(ix.indexrelid)"
                " FROM pg_index ix JOIN pg_class c ON c.oid = ix.indexrelid"
                " WHERE ix.indrelid = 'images'::regclass AND c.relname = ANY(:names)"
            ),
            {"names": [*_INDEXES, *_REPLACED]},
        )
        return {name: (valid, definition) for name, valid, definition in rows}


async def _revision(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


async def test_upgrade_rebuilds_an_index_a_failed_concurrent_build_left_invalid():
    url, engine = await _database_at_previous_revision()
    try:
        async with engine.connect() as conn:
            # No parent rows needed: skip FK enforcement and counter triggers.
            await conn.execute(text("SET session_replication_role = replica"))
            for name in ("dup-a", "dup-b"):
                await conn.execute(text(_INSERT_IMAGE), {"name": name})
            # A real failed concurrent build: it leaves its index behind, INVALID.
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX CONCURRENTLY idx_favorites_image_id ON images (favorites)"
                    )
                )
        assert (await _indexes(engine))["idx_favorites_image_id"][0] is False

        _alembic_upgrade(url, "head")

        indexes = await _indexes(engine)
        assert sorted(indexes) == sorted(_INDEXES)
        for name, definition in _INDEXES.items():
            valid, indexdef = indexes[name]
            assert valid, name
            assert indexdef == f"CREATE INDEX {name} ON public.images USING btree {definition}"
        assert await _revision(engine) == "0009_image_sort_seek_indexes"
    finally:
        await engine.dispose()


def _alembic_downgrade(url: str, revision: str) -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", "downgrade", revision],
        capture_output=True,
        text=True,
        env={**os.environ, "ALEMBIC_DB_URL": url},
    )
    assert result.returncode == 0, f"alembic downgrade {revision} failed:\n{result.stderr}"


async def test_upgrade_drops_the_indexes_the_composites_replace_and_downgrade_restores_them():
    url, engine = await _database_at_previous_revision()
    try:
        assert sorted(await _indexes(engine)) == sorted(_REPLACED)

        _alembic_upgrade(url, "head")
        assert sorted(await _indexes(engine)) == sorted(_INDEXES)

        _alembic_downgrade(url, _PREVIOUS_REVISION)
        indexes = await _indexes(engine)
        assert sorted(indexes) == sorted(_REPLACED)
        for name, definition in _REPLACED.items():
            valid, indexdef = indexes[name]
            assert valid, name
            assert indexdef == f"CREATE INDEX {name} ON public.images USING btree {definition}"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "squatter",
    [
        # On another table.
        "CREATE INDEX idx_last_post_image_id ON tags (tag_id)",
        # On images and valid, but not the index 0009 means: other column order...
        "CREATE INDEX idx_last_post_image_id ON images (last_post DESC, image_id)",
        # ...or partial.
        "CREATE INDEX idx_last_post_image_id ON images (last_post, image_id) WHERE status = 1",
    ],
)
async def test_upgrade_fails_rather_than_record_an_index_it_could_not_build(squatter: str):
    url, engine = await _database_at_previous_revision()
    try:
        # The name is taken by some other index, so IF NOT EXISTS skips it.
        async with engine.connect() as conn:
            await conn.execute(text(squatter))

        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            env={**os.environ, "ALEMBIC_DB_URL": url},
        )

        assert result.returncode != 0
        assert "idx_last_post_image_id" in result.stderr
        assert await _revision(engine) == _PREVIOUS_REVISION
        # Nothing is dropped until every replacement is in place.
        assert set(_REPLACED) <= set(await _indexes(engine))
    finally:
        await engine.dispose()


def test_offline_sql_still_renders_the_builds():
    """`alembic upgrade --sql` has no database to inspect, so it emits the builds alone."""
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", f"{_PREVIOUS_REVISION}:head", "--sql"],
        capture_output=True,
        text=True,
        env={**os.environ, "ALEMBIC_DB_URL": TEST_DATABASE_URL},
    )

    assert result.returncode == 0, result.stderr
    for name, definition in _INDEXES.items():
        assert (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON images {definition}"
            in result.stdout
        )
    for name in _REPLACED:
        assert f"DROP INDEX CONCURRENTLY IF EXISTS {name}" in result.stdout
