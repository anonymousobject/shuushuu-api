"""Group membership changes take effect immediately despite the permission cache.

Permissions are cached per user in Redis (``user_permissions:{id}``). Each test warms the
subject's cache with a real request before the change: a cold cache rebuilds from the DB
and would hide a missing invalidation. Uses the real-Redis client, since the default
``client`` fixture's mock Redis never stores anything and so can't hold a stale entry.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users
from tests.api.v1.test_feed_count import _login, _user

REPORTS_URL = "/api/v1/admin/reports"


async def _group(db: AsyncSession, title: str, *perm_titles: str) -> Groups:
    group = Groups(title=title, desc=title)
    db.add(group)
    await db.flush()
    for perm_title in perm_titles:
        perm = (
            await db.execute(select(Perms).where(Perms.title == perm_title))
        ).scalar_one_or_none()
        if perm is None:
            perm = Perms(title=perm_title, desc=perm_title)
            db.add(perm)
            await db.flush()
        db.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    await db.commit()
    await db.refresh(group)
    return group


async def _join(db: AsyncSession, user: Users, group: Groups) -> None:
    db.add(UserGroups(user_id=user.user_id, group_id=group.group_id))
    await db.commit()


async def _headers(client: AsyncClient, user: Users) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {await _login(client, user.username)}"}
    client.cookies.clear()
    return headers


async def _my_permissions(client: AsyncClient, headers: dict[str, str]) -> list[str]:
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200
    return response.json()["permissions"]


@pytest.mark.api
class TestGroupMembershipPermissionCache:
    async def _actors(
        self, client: AsyncClient, db: AsyncSession, name: str, subject_is_member: bool
    ):
        admin = await _user(db, f"{name}Admin")
        await _join(db, admin, await _group(db, f"{name}Admins", Permission.GROUP_MANAGE.value))
        subject = await _user(db, f"{name}Subject")
        mods = await _group(db, f"{name}Mods", Permission.REPORT_VIEW.value)
        if subject_is_member:
            await _join(db, subject, mods)
        return (
            mods,
            subject,
            await _headers(client, admin),
            await _headers(client, subject),
        )

    async def test_removing_member_revokes_permissions_immediately(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        client = client_real_redis
        mods, subject, admin_headers, subject_headers = await self._actors(
            client, db_session, "gcRemove", True
        )
        assert "report_view" in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 200

        response = await client.delete(
            f"/api/v1/admin/groups/{mods.group_id}/members/{subject.user_id}",
            headers=admin_headers,
        )

        assert response.status_code == 204
        assert "report_view" not in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 403

    async def test_adding_member_grants_permissions_immediately(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        client = client_real_redis
        mods, subject, admin_headers, subject_headers = await self._actors(
            client, db_session, "gcAdd", False
        )
        assert "report_view" not in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 403

        response = await client.post(
            f"/api/v1/admin/groups/{mods.group_id}/members/{subject.user_id}",
            headers=admin_headers,
        )

        assert response.status_code == 201
        assert "report_view" in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 200

    async def test_deleting_group_revokes_members_permissions_immediately(
        self, client_real_redis: AsyncClient, db_session: AsyncSession, redis_client
    ):
        client = client_real_redis
        mods, subject, admin_headers, subject_headers = await self._actors(
            client, db_session, "gcDelete", True
        )
        assert "report_view" in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 200

        response = await client.delete(
            f"/api/v1/admin/groups/{mods.group_id}", headers=admin_headers
        )

        assert response.status_code == 204
        assert "report_view" not in await _my_permissions(client, subject_headers)
        assert (await client.get(REPORTS_URL, headers=subject_headers)).status_code == 403
