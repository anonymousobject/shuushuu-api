"""Tests for GET /images/{image_id}/reports — mod-only per-image reports."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ReportStatus
from app.core.security import get_password_hash
from app.models.image import Images
from app.models.image_report import ImageReports
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.user import Users


async def _make_user(db, username, password="TestPassword123!"):
    user = Users(username=username, password=get_password_hash(password),
                 password_type="bcrypt", salt="", email=f"{username}@example.com", active=1)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _grant(db, user_id, perm_title):
    perm = (await db.execute(select(Perms).where(Perms.title == perm_title))).scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=perm_title)
        db.add(perm)
        await db.flush()
    group = Groups(title=f"{perm_title}_{user_id}", desc="g")
    db.add(group)
    await db.flush()
    db.add(GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1))
    db.add(UserGroups(user_id=user_id, group_id=group.group_id))
    await db.commit()


async def _login(client, username, password="TestPassword123!"):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


async def _img_with_report(db, owner, md5):
    img = Images(filename="rep", ext="jpg", md5_hash=md5, user_id=owner.user_id,
                 width=10, height=10, filesize=100, status=1)
    db.add(img)
    await db.commit()
    await db.refresh(img)
    report = ImageReports(image_id=img.image_id, user_id=owner.user_id, category=2,
                          reason_text="looks AI", status=ReportStatus.PENDING)
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return img, report


@pytest.mark.api
class TestGetImageReports:
    async def test_mod_sees_image_reports(self, client: AsyncClient, db_session: AsyncSession):
        owner = await _make_user(db_session, "irowner")
        img, report = await _img_with_report(db_session, owner, "a" * 32)
        mod = await _make_user(db_session, "irmod")
        await _grant(db_session, mod.user_id, "report_view")
        token = await _login(client, mod.username)

        r = await client.get(
            f"/api/v1/images/{img.image_id}/reports",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["report_id"] == report.report_id
        assert item["category"] == 2
        assert item["reason_text"] == "looks AI"
        assert item["status"] == ReportStatus.PENDING
        assert item["user"]["username"] == "irowner"

    async def test_regular_user_forbidden(self, client: AsyncClient, db_session: AsyncSession):
        owner = await _make_user(db_session, "irowner2")
        img, _ = await _img_with_report(db_session, owner, "b" * 32)
        user = await _make_user(db_session, "irplain")
        token = await _login(client, user.username)
        r = await client.get(
            f"/api/v1/images/{img.image_id}/reports",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_anonymous_forbidden(self, client: AsyncClient, db_session: AsyncSession):
        owner = await _make_user(db_session, "irowner3")
        img, _ = await _img_with_report(db_session, owner, "c" * 32)
        r = await client.get(f"/api/v1/images/{img.image_id}/reports")
        assert r.status_code in (401, 403)

    async def test_404_for_nonexistent_image(self, client: AsyncClient, db_session: AsyncSession):
        mod = await _make_user(db_session, "irmod4")
        await _grant(db_session, mod.user_id, "report_view")
        token = await _login(client, mod.username)
        r = await client.get(
            "/api/v1/images/99999999/reports",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404
