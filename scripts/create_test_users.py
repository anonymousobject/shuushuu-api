#!/usr/bin/env python3
"""
Create the dev test accounts in the configured database.

The last step of scripts/restore_prod_db.py, run inside the api container so
it sees the same DATABASE_URL the application does:

    docker compose run --rm -T --no-deps api \\
        uv run --no-project python scripts/create_test_users.py

Accounts go in through the Users model, not raw SQL: the model's Python-side
defaults fill the many NOT NULL columns that have no database default, and the
hash is whatever app.core.security verifies. Existing accounts are left alone,
so re-running is harmless.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models.permissions import Groups, UserGroups  # noqa: E402
from app.models.user import Users  # noqa: E402

# Typed as list[dict[str, Any]] to avoid mypy issues with mixed value types
TEST_ACCOUNTS: list[dict[str, Any]] = [
    {
        "username": "testUser",
        "password": "shuutestuser",
        "email": "test1@shuushuu.com",
        "admin": 0,
        "group": None,
    },
    {
        "username": "testadmin",
        "password": "shuutestadmin",
        "email": "testadmin@shuushuu.com",
        "admin": 1,
        "group": "Admins",
    },
    {
        "username": "testmod",
        "password": "shuutestmod",
        "email": "testmod@shuushuu.com",
        "admin": 0,
        "group": "Mods",
    },
    {
        "username": "testtagger",
        "password": "shuutesttagger",
        "email": "testtagger@shuushuu.com",
        "admin": 0,
        "group": "Taggers",
    },
]


async def create_test_users(db: AsyncSession) -> list[str]:
    """
    Create any test account that does not exist yet.

    Returns:
        Usernames created on this run (existing accounts are skipped).
    """
    created: list[str] = []
    for account in TEST_ACCOUNTS:
        username = account["username"]
        existing = await db.scalar(
            select(Users.user_id).where(Users.username == username)  # type: ignore[call-overload]
        )
        if existing is not None:
            print(f"  - {username} already exists")
            continue

        user = Users(
            username=username,
            password=get_password_hash(account["password"]),
            password_type="bcrypt",
            salt="",
            email=account["email"],
            active=1,
            email_verified=True,
            admin=account["admin"],
        )
        db.add(user)
        await db.flush()

        group_title = account["group"]
        if group_title:
            group_id = await db.scalar(
                select(Groups.group_id).where(Groups.title == group_title)  # type: ignore[call-overload]
            )
            if group_id is None:
                print(f"  ⚠️  group '{group_title}' not found; {username} created without it")
            else:
                db.add(UserGroups(user_id=user.user_id, group_id=group_id))

        role = f"admin={account['admin']}, group={group_title or 'none'}"
        print(f"  ✓ {username} created ({role}, password: {account['password']})")
        created.append(username)

    await db.commit()
    return created


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as db:
            created = await create_test_users(db)
        print(f"{len(created)} test user(s) created")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
