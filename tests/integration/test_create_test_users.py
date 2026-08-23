"""Tests for scripts/create_test_users.py — the dev accounts a prod restore adds.

The accounts go in through the Users model rather than hand-written SQL so the
model's Python-side defaults fill the NOT NULL columns that have no database
default (a raw INSERT that omits one fails on Postgres) and the password hash
is whatever the app verifies.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.permissions import Groups, UserGroups
from app.models.user import Users
from scripts.create_test_users import TEST_ACCOUNTS, create_test_users

# The test database is seeded with user_id=1 "testuser" (tests/conftest.py);
# usernames are case-insensitive, so the script must treat "testUser" as
# already present and create only the other three.
EXPECTED_CREATED = ["testadmin", "testmod", "testtagger"]


async def _seed_groups(db: AsyncSession) -> dict[str, int]:
    groups = {title: Groups(title=title, desc=title) for title in ("Admins", "Mods", "Taggers")}
    db.add_all(groups.values())
    await db.flush()
    return {title: group.group_id for title, group in groups.items() if group.group_id}


async def _group_titles(db: AsyncSession, username: str) -> set[str]:
    result = await db.execute(
        select(Groups.title)
        .join(UserGroups, UserGroups.group_id == Groups.group_id)
        .join(Users, Users.user_id == UserGroups.user_id)
        .where(Users.username == username)
    )
    return set(result.scalars().all())


async def test_creates_every_account_with_a_verifiable_password(db_session: AsyncSession):
    await _seed_groups(db_session)

    created = await create_test_users(db_session)

    assert created == EXPECTED_CREATED
    for account in TEST_ACCOUNTS:
        if account["username"] not in EXPECTED_CREATED:
            continue
        user = (
            await db_session.execute(select(Users).where(Users.username == account["username"]))
        ).scalar_one()
        assert verify_password(account["password"], user.password)
        assert user.password_type == "bcrypt"
        assert user.active == 1
        assert user.email_verified is True
        assert user.admin == account["admin"]


async def test_assigns_group_membership(db_session: AsyncSession):
    await _seed_groups(db_session)

    await create_test_users(db_session)

    assert await _group_titles(db_session, "testadmin") == {"Admins"}
    assert await _group_titles(db_session, "testmod") == {"Mods"}
    assert await _group_titles(db_session, "testtagger") == {"Taggers"}
    assert await _group_titles(db_session, "testUser") == set()


async def test_is_idempotent(db_session: AsyncSession):
    await _seed_groups(db_session)
    await create_test_users(db_session)
    user_count = await db_session.scalar(select(func.count()).select_from(Users))

    created = await create_test_users(db_session)

    assert created == []
    assert await db_session.scalar(select(func.count()).select_from(Users)) == user_count
    assert await _group_titles(db_session, "testadmin") == {"Admins"}


async def test_matches_existing_accounts_case_insensitively(db_session: AsyncSession):
    """The seeded "testuser" stands in for a prod account that differs only in
    case; creating "testUser" beside it would be a duplicate natural key."""
    created = await create_test_users(db_session)

    assert "testUser" not in created
    count = await db_session.scalar(
        select(func.count()).select_from(Users).where(Users.username == "testuser")
    )
    assert count == 1


async def test_skips_membership_when_the_group_is_missing(db_session: AsyncSession):
    """A dump without the group must not abort the restore's last step."""
    created = await create_test_users(db_session)

    assert "testadmin" in created
    assert await _group_titles(db_session, "testadmin") == set()
