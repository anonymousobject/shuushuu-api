"""Admin group and permission search treat LIKE metacharacters literally."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.permissions import Perms
from tests.api.v1.test_admin_group_membership_cache import _group, _headers, _join
from tests.api.v1.test_feed_count import _user


@pytest.mark.api
class TestAdminSearchEscaping:
    async def test_group_search_matches_underscore_literally(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        admin = await _user(db_session, "escGroupAdmin")
        await _join(
            db_session,
            admin,
            await _group(db_session, "escGroupAdmins", Permission.GROUP_MANAGE.value),
        )
        await _group(db_session, "escgrp a_b")
        await _group(db_session, "escgrp axb")

        response = await client.get(
            "/api/v1/admin/groups",
            params={"search": "escgrp a_b"},
            headers=await _headers(client, admin),
        )

        assert response.status_code == 200
        assert [g["title"] for g in response.json()["groups"]] == ["escgrp a_b"]
        assert response.json()["total"] == 1

    async def test_permission_search_matches_percent_literally(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        admin = await _user(db_session, "escPermAdmin")
        await _join(
            db_session,
            admin,
            await _group(db_session, "escPermAdmins", Permission.GROUP_PERM_MANAGE.value),
        )
        db_session.add_all(
            [
                Perms(title="escperm a%b", desc="literal percent"),
                Perms(title="escperm axxb", desc="would match a wildcard"),
            ]
        )
        await db_session.commit()

        response = await client.get(
            "/api/v1/admin/permissions",
            params={"search": "escperm a%b"},
            headers=await _headers(client, admin),
        )

        assert response.status_code == 200
        assert [p["title"] for p in response.json()["permissions"]] == ["escperm a%b"]
        assert response.json()["total"] == 1
