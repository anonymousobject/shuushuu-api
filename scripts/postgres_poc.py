"""Postgres proof-of-concept: build the schema and smoke-test the real API.

Part of docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md. Runs the real
FastAPI app in-process (httpx ASGI transport, no dependency overrides, real
Redis from the dev stack) against the Postgres container from
docker-compose.postgres.yml. MariaDB is untouched.

    docker compose -f docker-compose.postgres.yml up -d
    uv run python scripts/postgres_poc.py setup   # drop + create_all schema
    uv run python scripts/postgres_poc.py smoke   # seed + exercise endpoints

The database URL can be overridden with POSTGRES_POC_DATABASE_URL.
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from typing import Any

DEFAULT_URL = "postgresql+asyncpg://shuushuu:pg_dev_password@localhost:5432/shuushuu"

# Must be set before any app.* import: app.config caches settings at import
# time and app.core.database builds the engine from them.
os.environ["DATABASE_URL"] = os.environ.get("POSTGRES_POC_DATABASE_URL", DEFAULT_URL)

SMOKE_USERNAME = "pocuser"
SMOKE_PASSWORD = "poc-password-123"  # noqa: S105 - throwaway POC credential


def _dedupe_index_names(metadata: Any) -> None:
    """Rename index names that repeat across tables.

    MySQL scopes index names per table; Postgres per schema. The legacy schema
    reuses a few names (idx_date, idx_tag_id), which is fine on MariaDB but a
    DuplicateTableError on Postgres. POC-only shim: a real migration would
    normalize the names in a Postgres baseline migration instead.
    """
    by_name = defaultdict(list)
    for table in metadata.tables.values():
        for index in table.indexes:
            by_name[index.name].append((table, index))
    for entries in by_name.values():
        if len(entries) > 1:
            for table, index in entries:
                index.name = f"{table.name}_{index.name}"


async def setup() -> int:
    """Drop and recreate the full schema on the POC database via create_all."""
    from sqlalchemy import text
    from sqlmodel import SQLModel

    # app.main, not app.models: the models package __init__ does not import
    # every model module (e.g. user_suspension), but the app wiring does.
    import app.main  # noqa: F401  (registers all tables on SQLModel.metadata)
    from app.core.database import engine

    _dedupe_index_names(SQLModel.metadata)
    async with engine.begin() as conn:
        # DROP SCHEMA CASCADE instead of drop_all: the FK graph has cycles
        # that Postgres won't untangle without CASCADE (MySQL drop_all just
        # disables FK checks).
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        # citext lives in public, so the DROP SCHEMA above removed it; the
        # username/email/tag-title columns need it (see types.ci_string).
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(SQLModel.metadata.create_all)
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            )
        ).scalar()
    await engine.dispose()
    print(f"setup: created {count} tables on {os.environ['DATABASE_URL']}")
    return 0


async def _seed() -> tuple[int, int]:
    """Seed a login-capable user, a tag, and an image. Idempotent."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.permission_sync import sync_permissions
    from app.core.security import get_password_hash
    from app.models.image import Images
    from app.models.tag import Tags
    from app.models.user import Users

    async with AsyncSessionLocal() as db:
        await sync_permissions(db)

        user = (
            await db.execute(select(Users).where(Users.username == SMOKE_USERNAME))  # type: ignore[arg-type]
        ).scalar_one_or_none()
        if user is None:
            user = Users(
                username=SMOKE_USERNAME,
                password=get_password_hash(SMOKE_PASSWORD),
                password_type="bcrypt",
                salt="pocsalt123456789",
                email="poc@example.com",
                active=1,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        image = (
            await db.execute(select(Images).where(Images.filename == "poc-image-001"))  # type: ignore[arg-type]
        ).scalar_one_or_none()
        if image is None:
            image = Images(
                filename="poc-image-001",
                ext="jpg",
                original_filename="poc.jpg",
                md5_hash="d41d8cd98f00b204e9800998ecf8427e",
                filesize=123456,
                width=1920,
                height=1080,
                caption="Postgres POC seed image",
                rating=0.0,
                user_id=user.user_id,
                status=1,
                locked=False,
            )
            db.add(image)

        tag = (
            await db.execute(select(Tags).where(Tags.title == "poc tag"))  # type: ignore[arg-type]
        ).scalar_one_or_none()
        if tag is None:
            db.add(Tags(title="poc tag", type=1, user_id=user.user_id))

        await db.commit()

        image_id = (
            await db.execute(select(Images.image_id).where(Images.filename == "poc-image-001"))  # type: ignore[call-overload]
        ).scalar_one()
        assert user.user_id is not None
        return user.user_id, image_id


async def _service_checks(
    results: list[tuple[str, bool, str]], user_id: int, image_id: int
) -> None:
    """Run the dialect-branched service SQL that the endpoint checks don't reach.

    Real session, real Postgres — proves the PG twins of the tag-type-flags
    recompute and the repost INSERT ... ON CONFLICT actually execute.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.image import Images
    from app.models.tag import Tags
    from app.models.tag_link import TagLinks
    from app.services.repost import migrate_repost_data
    from app.services.tag_type_flags import refresh_images_tag_type_flags

    async def run(name: str, coro: Any) -> None:
        try:
            await coro
            results.append((name, True, "ok"))
        except Exception as exc:  # noqa: BLE001 - a failing check must not stop the run
            results.append((name, False, f"{type(exc).__name__}: {str(exc)[:200]}"))

    async with AsyncSessionLocal() as db:

        async def flags_check() -> None:
            tag_id = (
                await db.execute(select(Tags.tag_id).where(Tags.title == "poc tag"))  # type: ignore[call-overload]
            ).scalar_one()
            link = (
                await db.execute(
                    select(TagLinks).where(
                        TagLinks.image_id == image_id,  # type: ignore[arg-type]
                        TagLinks.tag_id == tag_id,
                    )
                )
            ).scalar_one_or_none()
            if link is None:
                db.add(TagLinks(image_id=image_id, tag_id=tag_id))
            await refresh_images_tag_type_flags(db, [image_id])
            await db.commit()
            has_theme = (
                await db.execute(
                    select(Images.has_theme).where(Images.image_id == image_id)  # type: ignore[call-overload]
                )
            ).scalar_one()
            assert has_theme, "has_theme not set by recompute"

        async def repost_check() -> None:
            repost = Images(
                filename="poc-image-repost",
                ext="jpg",
                original_filename="poc2.jpg",
                md5_hash="e41d8cd98f00b204e9800998ecf8427f",
                filesize=1,
                width=1,
                height=1,
                caption="Postgres POC repost",
                rating=0.0,
                user_id=user_id,
                status=1,
                locked=False,
            )
            db.add(repost)
            await db.commit()
            await db.refresh(repost)
            assert repost.image_id is not None
            counts = await migrate_repost_data(repost.image_id, image_id, db)
            await db.commit()
            assert counts["tags_moved"] == 0  # repost had no tags; the SQL still ran

        await run("service: tag_type_flags recompute (PG SQL)", flags_check())
        await run("service: repost migrate (ON CONFLICT SQL)", repost_check())


async def smoke() -> int:
    """Exercise the real app against Postgres and report per-check results."""
    from httpx import ASGITransport, AsyncClient

    from app.core.database import engine
    from app.main import app

    user_id, image_id = await _seed()

    results: list[tuple[str, bool, str]] = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://poc") as client:

        async def check(name: str, coro: Any, expect: int) -> Any:
            try:
                response = await coro
            except Exception as exc:  # noqa: BLE001 - a failing check must not stop the run
                results.append((name, False, f"{type(exc).__name__}: {exc}"))
                return None
            ok = response.status_code == expect
            detail = f"HTTP {response.status_code}"
            if not ok:
                detail += f" (expected {expect}): {response.text[:200]}"
            results.append((name, ok, detail))
            return response if ok else None

        await check("GET /health", client.get("/health"), 200)

        login = await check(
            "POST /api/v1/auth/login",
            client.post(
                "/api/v1/auth/login",
                json={"username": SMOKE_USERNAME, "password": SMOKE_PASSWORD},
            ),
            200,
        )
        headers = {}
        if login is not None:
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            await check("GET /api/v1/auth/me", client.get("/api/v1/auth/me", headers=headers), 200)

        await check("GET /api/v1/images", client.get("/api/v1/images"), 200)
        await check(f"GET /api/v1/images/{image_id}", client.get(f"/api/v1/images/{image_id}"), 200)
        await check("GET /api/v1/tags", client.get("/api/v1/tags"), 200)
        await check(
            "GET /api/v1/tags?search= (DB fallback)",
            client.get("/api/v1/tags", params={"search": "poc"}),
            200,
        )

        if headers:
            await check(
                "POST /api/v1/comments",
                client.post(
                    "/api/v1/comments",
                    json={"image_id": image_id, "post_text": "Postgres POC comment"},
                    headers=headers,
                ),
                201,
            )
        await check(
            "GET /api/v1/comments?image_id",
            client.get("/api/v1/comments", params={"image_id": image_id}),
            200,
        )
        await check(
            "GET /api/v1/comments?search_text= (DB fallback)",
            client.get("/api/v1/comments", params={"search_text": "postgres"}),
            200,
        )
        if headers:
            await check(
                "GET /api/v1/images/recommended (cold start)",
                client.get("/api/v1/images/recommended", headers=headers),
                200,
            )

    await _service_checks(results, user_id, image_id)

    await engine.dispose()

    width = max(len(name) for name, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        failed += 0 if ok else 1
        print(f"{status}  {name:<{width}}  {detail}")
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "smoke"])
    args = parser.parse_args()
    if args.command == "setup":
        return asyncio.run(setup())
    return asyncio.run(smoke())


if __name__ == "__main__":
    sys.exit(main())
