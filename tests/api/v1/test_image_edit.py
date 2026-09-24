"""Tests for PATCH /api/v1/images/{image_id} endpoint."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ImageStatus, TagType
from app.core.security import create_access_token, get_password_hash
from app.models.favorite import Favorites
from app.models.image import Images
from app.models.image_metadata_history import ImageMetadataHistory
from app.models.image_status_history import ImageStatusHistory
from app.models.permissions import GroupPerms, Groups, Perms, UserGroups
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from tests.transient_conflict import _deadlock_error, _flaky_commit


async def create_user(
    db_session: AsyncSession,
    username: str = "edituser",
    email: str = "edit@example.com",
    admin: int = 0,
) -> Users:
    """Create a user for testing."""
    user = Users(
        username=username,
        password=get_password_hash("TestPassword123!"),
        password_type="bcrypt",
        salt="saltsalt12345678",
        email=email,
        active=1,
        admin=admin,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def create_image(
    db_session: AsyncSession,
    user_id: int,
    caption: str = "original caption",
    miscmeta: str | None = None,
    source_url: str | None = None,
) -> Images:
    """Create a test image."""
    image = Images(
        filename="test-edit-001",
        ext="jpg",
        original_filename="test.jpg",
        md5_hash="abcdef1234567890abcdef1234567890",
        filesize=100000,
        width=800,
        height=600,
        caption=caption,
        miscmeta=miscmeta,
        source_url=source_url,
        user_id=user_id,
        status=1,
    )
    db_session.add(image)
    await db_session.commit()
    await db_session.refresh(image)
    return image


async def grant_permission(db_session: AsyncSession, user_id: int, perm_title: str):
    """Grant a permission to a user via a group."""
    result = await db_session.execute(select(Perms).where(Perms.title == perm_title))
    perm = result.scalar_one_or_none()
    if not perm:
        perm = Perms(title=perm_title, desc=f"Test permission {perm_title}")
        db_session.add(perm)
        await db_session.flush()

    result = await db_session.execute(select(Groups).where(Groups.title == "edit_test_group"))
    group = result.scalar_one_or_none()
    if not group:
        group = Groups(title="edit_test_group", desc="Image edit test group")
        db_session.add(group)
        await db_session.flush()

    result = await db_session.execute(
        select(GroupPerms).where(
            GroupPerms.group_id == group.group_id, GroupPerms.perm_id == perm.perm_id
        )
    )
    if not result.scalar_one_or_none():
        group_perm = GroupPerms(group_id=group.group_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(group_perm)
        await db_session.flush()

    result = await db_session.execute(
        select(UserGroups).where(
            UserGroups.user_id == user_id, UserGroups.group_id == group.group_id
        )
    )
    if not result.scalar_one_or_none():
        user_group = UserGroups(user_id=user_id, group_id=group.group_id)
        db_session.add(user_group)

    await db_session.commit()


def auth_header(user: Users) -> dict[str, str]:
    """Create an Authorization header for a user."""
    token = create_access_token(user.user_id)
    return {"Authorization": f"Bearer {token}"}


class TestImageEdit:
    """Tests for PATCH /api/v1/images/{image_id}."""

    @pytest.mark.asyncio
    async def test_owner_can_update_caption(self, client: AsyncClient, db_session: AsyncSession):
        """Image owner can update their image's caption."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "new caption"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "new caption"

    @pytest.mark.asyncio
    async def test_owner_can_update_miscmeta(self, client: AsyncClient, db_session: AsyncSession):
        """Image owner can update their image's miscmeta."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "pixiv: 99999"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        assert response.json()["miscmeta"] == "pixiv: 99999"

    @pytest.mark.asyncio
    async def test_non_owner_without_permission_gets_403(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Non-owner without IMAGE_EDIT_META permission cannot edit."""
        owner = await create_user(db_session, username="owner", email="owner@test.com")
        other = await create_user(db_session, username="other", email="other@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "hacked"},
            headers=auth_header(other),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_user_with_image_edit_meta_permission_can_edit(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """User with IMAGE_EDIT_META permission can edit any image."""
        owner = await create_user(db_session, username="owner2", email="owner2@test.com")
        mod = await create_user(db_session, username="mod", email="mod@test.com")
        image = await create_image(db_session, owner.user_id, caption="before")

        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "mod edited"},
            headers=auth_header(mod),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "mod edited"

    @pytest.mark.asyncio
    async def test_admin_can_edit_any_image(self, client: AsyncClient, db_session: AsyncSession):
        """Admin users can edit any image."""
        owner = await create_user(db_session, username="owner3", email="owner3@test.com")
        admin = await create_user(db_session, username="admin", email="admin@test.com", admin=1)
        image = await create_image(db_session, owner.user_id, caption="before")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "admin edited"},
            headers=auth_header(admin),
        )

        assert response.status_code == 200
        assert response.json()["caption"] == "admin edited"

    @pytest.mark.asyncio
    async def test_empty_update_returns_400(self, client: AsyncClient, db_session: AsyncSession):
        """Empty update body (no fields set) returns 400."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={},
            headers=auth_header(owner),
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_image_not_found_returns_404(self, client: AsyncClient, db_session: AsyncSession):
        """Editing a nonexistent image returns 404."""
        user = await create_user(db_session)

        response = await client.patch(
            "/api/v1/images/999999",
            json={"caption": "ghost"},
            headers=auth_header(user),
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_partial_update_only_changes_provided_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Setting caption does not clear miscmeta, and vice versa."""
        owner = await create_user(db_session)
        image = await create_image(
            db_session, owner.user_id, caption="keep me", miscmeta="keep me too"
        )

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "changed"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["caption"] == "changed"
        assert data["miscmeta"] == "keep me too"


class TestImageOwnerStatusChange:
    """Tests for owner setting their own image status to spoiler or repost."""

    @pytest.mark.asyncio
    async def test_owner_can_mark_active_image_as_spoiler(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Owner can set their ACTIVE image to SPOILER status."""
        owner = await create_user(db_session, username="spoiler1", email="sp1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": 2},  # SPOILER
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        assert response.json()["status"] == 2

    @pytest.mark.asyncio
    async def test_owner_can_mark_active_image_as_repost(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Owner can set their ACTIVE image to REPOST status with replacement_id."""
        owner = await create_user(db_session, username="repost1", email="rp1@test.com")
        original = await create_image(db_session, owner.user_id)
        repost = await create_image(
            db_session,
            owner.user_id,
            caption="repost img",
        )
        # Use a different md5 hash for the second image to keep test data distinct
        repost.md5_hash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        await db_session.commit()

        response = await client.patch(
            f"/api/v1/images/{repost.image_id}",
            json={"status": -1, "replacement_id": original.image_id},  # REPOST
            headers=auth_header(owner),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == -1
        assert data["replacement_id"] == original.image_id

    @pytest.mark.asyncio
    async def test_owner_cannot_mark_non_active_image(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Owner cannot change status of a non-ACTIVE image."""
        owner = await create_user(db_session, username="nonactive1", email="na1@test.com")
        image = await create_image(db_session, owner.user_id)
        image.status = 2  # Already SPOILER
        await db_session.commit()

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": -1, "replacement_id": 999},
            headers=auth_header(owner),
        )

        assert response.status_code == 400
        assert "active" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_owner_cannot_set_disallowed_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Owner cannot set status to values other than SPOILER or REPOST."""
        owner = await create_user(db_session, username="badstatus1", email="bs1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": -2},  # INAPPROPRIATE — not allowed for owners
            headers=auth_header(owner),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_repost_requires_replacement_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Marking as repost without replacement_id fails."""
        owner = await create_user(db_session, username="norepl1", email="nr1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": -1},  # REPOST without replacement_id
            headers=auth_header(owner),
        )

        assert response.status_code == 400
        assert "replacement_id" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_repost_of_self_fails(self, client: AsyncClient, db_session: AsyncSession):
        """Cannot mark an image as a repost of itself."""
        owner = await create_user(db_session, username="selfr1", email="sr1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": -1, "replacement_id": image.image_id},
            headers=auth_header(owner),
        )

        assert response.status_code == 400
        assert "itself" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_repost_with_nonexistent_replacement_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Marking as repost with nonexistent replacement_id fails."""
        owner = await create_user(db_session, username="badrepl1", email="br1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": -1, "replacement_id": 999999},
            headers=auth_header(owner),
        )

        assert response.status_code == 404
        assert "original" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_non_owner_cannot_set_status(self, client: AsyncClient, db_session: AsyncSession):
        """Non-owner without admin/permissions cannot change image status."""
        owner = await create_user(db_session, username="statusown1", email="so1@test.com")
        other = await create_user(db_session, username="statusoth1", email="so2@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": 2},  # SPOILER
            headers=auth_header(other),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_locked_image_cannot_have_status_changed_by_owner(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Owner cannot change status of a locked image."""
        owner = await create_user(db_session, username="locked1", email="lk1@test.com")
        image = await create_image(db_session, owner.user_id)
        image.locked = 1
        await db_session.commit()

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": 2},  # SPOILER
            headers=auth_header(owner),
        )

        assert response.status_code == 400
        assert "locked" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_status_change_creates_history_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Status change by owner creates an ImageStatusHistory entry."""
        from app.models.image_status_history import ImageStatusHistory

        owner = await create_user(db_session, username="hist1", email="h1@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"status": 2},  # SPOILER
            headers=auth_header(owner),
        )

        assert response.status_code == 200

        result = await db_session.execute(
            select(ImageStatusHistory).where(ImageStatusHistory.image_id == image.image_id)
        )
        history = result.scalar_one()
        assert history.old_status == 1  # ACTIVE
        assert history.new_status == 2  # SPOILER
        assert history.user_id == owner.user_id


class TestImageEditSourceAndMiscmeta:
    """PATCH /api/v1/images/{image_id}: source_url and miscmeta validation."""

    @pytest.mark.asyncio
    async def test_owner_can_set_source_url(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "  https://www.pixiv.net/artworks/1  "},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == "https://www.pixiv.net/artworks/1"

    @pytest.mark.asyncio
    async def test_mod_with_image_edit_meta_can_set_source_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session, username="srcowner", email="srcowner@test.com")
        mod = await create_user(db_session, username="srcmod", email="srcmod@test.com")
        image = await create_image(db_session, owner.user_id)
        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(mod),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == "https://example.com/art"

    @pytest.mark.asyncio
    async def test_blank_source_url_clears_it(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "   "},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] is None

    @pytest.mark.asyncio
    async def test_non_http_source_url_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "javascript:alert(1)"},
            headers=auth_header(owner),
        )

        assert response.status_code == 422, response.text
        # No "Value error, " prefix: the frontend shows this message verbatim.
        assert (
            response.json()["detail"][0]["msg"] == "source_url must start with http:// or https://"
        )
        await db_session.refresh(image)
        assert image.source_url == "https://example.com/a"

    @pytest.mark.asyncio
    async def test_source_url_length_boundary(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)
        prefix = "https://example.com/"
        at_limit = prefix + "a" * (2000 - len(prefix))

        ok = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": at_limit},
            headers=auth_header(owner),
        )
        too_long = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": at_limit + "a"},
            headers=auth_header(owner),
        )

        assert ok.status_code == 200, ok.text
        assert ok.json()["source_url"] == at_limit
        assert too_long.status_code == 422, too_long.text

    @pytest.mark.asyncio
    async def test_non_editor_cannot_set_source_url(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session, username="srcowner2", email="srcowner2@test.com")
        other = await create_user(db_session, username="srcother", email="srcother@test.com")
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(other),
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_miscmeta_trimmed_and_blank_clears(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="old")

        trimmed = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "  circle: foo  "},
            headers=auth_header(owner),
        )
        cleared = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "   "},
            headers=auth_header(owner),
        )

        assert trimmed.status_code == 200, trimmed.text
        assert trimmed.json()["miscmeta"] == "circle: foo"
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["miscmeta"] is None

    @pytest.mark.asyncio
    async def test_overlong_miscmeta_rejected(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "a" * 256},
            headers=auth_header(owner),
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_miscmeta_at_limit_with_padding_is_trimmed_then_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)
        at_limit = "a" * 255

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": " " + at_limit + "\n"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["miscmeta"] == at_limit

    @pytest.mark.asyncio
    async def test_source_url_at_limit_with_padding_is_trimmed_then_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)
        prefix = "https://example.com/"
        at_limit = prefix + "a" * (2000 - len(prefix))

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": " " + at_limit + "\n"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == at_limit


async def history_rows(db_session: AsyncSession, image_id: int) -> list[ImageMetadataHistory]:
    result = await db_session.execute(
        select(ImageMetadataHistory)
        .where(ImageMetadataHistory.image_id == image_id)
        .order_by(ImageMetadataHistory.id)
    )
    return list(result.scalars().all())


class TestImageEditHistory:
    """PATCH /api/v1/images/{image_id} records miscmeta/source_url changes."""

    @pytest.mark.asyncio
    async def test_setting_source_url_writes_one_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        rows = await history_rows(db_session, image.image_id)
        assert [(r.field, r.old_value, r.new_value, r.user_id) for r in rows] == [
            ("source_url", None, "https://example.com/art", owner.user_id)
        ]
        assert rows[0].created_at is not None

    @pytest.mark.asyncio
    async def test_changing_both_fields_writes_two_rows(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(
            db_session, owner.user_id, miscmeta="old info", source_url="https://example.com/old"
        )

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "new info", "source_url": "https://example.com/new"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        rows = await history_rows(db_session, image.image_id)
        assert sorted((r.field, r.old_value, r.new_value) for r in rows) == [
            ("miscmeta", "old info", "new info"),
            ("source_url", "https://example.com/old", "https://example.com/new"),
        ]

    @pytest.mark.asyncio
    async def test_clearing_records_the_old_value(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, source_url="https://example.com/a")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": ""},
            headers=auth_header(owner),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [(r.field, r.old_value, r.new_value) for r in rows] == [
            ("source_url", "https://example.com/a", None)
        ]

    @pytest.mark.asyncio
    async def test_unchanged_value_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Covers a double submit, and a value that differs only by whitespace."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="same")

        first = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "same"},
            headers=auth_header(owner),
        )
        second = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "  same  "},
            headers=auth_header(owner),
        )

        assert first.status_code == 200 and second.status_code == 200
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_empty_string_to_blank_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A legacy '' and a cleared null both read as "none"; no visible change."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": ""},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_padded_value_matching_trimmed_new_value_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A legacy 'foo ' and a saved 'foo' read the same trimmed value; no visible change."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="foo ")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "foo"},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_whitespace_only_to_blank_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A legacy '  ' and a cleared null both read as "none"; no visible change."""
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="  ")

        response = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": ""},
            headers=auth_header(owner),
        )

        assert response.status_code == 200, response.text
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_legacy_empty_string_old_value_recorded_as_null(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id, miscmeta="")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "now set"},
            headers=auth_header(owner),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [(r.old_value, r.new_value) for r in rows] == [(None, "now set")]

    @pytest.mark.asyncio
    async def test_caption_change_writes_no_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(db_session)
        image = await create_image(db_session, owner.user_id)

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"caption": "new caption"},
            headers=auth_header(owner),
        )

        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_rejected_edits_write_no_row(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session, username="histowner", email="histowner@test.com")
        other = await create_user(db_session, username="histother", email="histother@test.com")
        image = await create_image(db_session, owner.user_id)

        forbidden = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "https://example.com/art"},
            headers=auth_header(other),
        )
        invalid = await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"source_url": "ftp://example.com/art"},
            headers=auth_header(owner),
        )

        assert forbidden.status_code == 403
        assert invalid.status_code == 422
        assert await history_rows(db_session, image.image_id) == []

    @pytest.mark.asyncio
    async def test_mod_edit_records_the_mod(self, client: AsyncClient, db_session: AsyncSession):
        owner = await create_user(db_session, username="histowner2", email="histowner2@test.com")
        mod = await create_user(db_session, username="histmod", email="histmod@test.com")
        image = await create_image(db_session, owner.user_id)
        await grant_permission(db_session, mod.user_id, "image_edit_meta")

        await client.patch(
            f"/api/v1/images/{image.image_id}",
            json={"miscmeta": "credited"},
            headers=auth_header(mod),
        )

        rows = await history_rows(db_session, image.image_id)
        assert [r.user_id for r in rows] == [mod.user_id]


class TestImageEditTransientConflictRetry:
    """PATCH /api/v1/images/{image_id} replays a transient conflict, not a 500.

    The image's row lock plus the repost migration's writes to a second image
    can deadlock: two images marked reposts of each other at once lock them in
    opposite orders. As for the admin status change (test_admin_images.py), the
    deadlock is injected into the unit's commit, which aborts the attempt with
    nothing persisted, exactly as a real one does.
    """

    @pytest.mark.asyncio
    @pytest.mark.needs_commit
    async def test_repost_with_metadata_retries_deadlock_and_applies_once(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The replay lands the edit, the repost migration and both history rows once.

        needs_commit: the retry performs a real transaction rollback; under the
        default SAVEPOINT isolation that rollback would unwind the fixture's
        committed rows too.
        """
        owner = await create_user(db_session, username="retryowner", email="retry@test.com")
        original = await create_image(db_session, owner.user_id)
        repost = await create_image(db_session, owner.user_id, miscmeta="old info")
        tag = Tags(title="retry repost tag", type=TagType.THEME, user_id=owner.user_id)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        db_session.add(TagLinks(image_id=repost.image_id, tag_id=tag.tag_id, user_id=owner.user_id))
        db_session.add(Favorites(image_id=repost.image_id, user_id=owner.user_id))
        await db_session.commit()

        # The route shares this session, so its retry rollback expires every
        # instance above. Hold the ids as plain ints for the assertions.
        repost_id, original_id, tag_id = repost.image_id, original.image_id, tag.tag_id
        headers = auth_header(owner)

        commit_patch, calls = _flaky_commit(1, _deadlock_error())
        with commit_patch:
            response = await client.patch(
                f"/api/v1/images/{repost_id}",
                json={
                    "status": ImageStatus.REPOST,
                    "replacement_id": original_id,
                    "miscmeta": "new info",
                },
                headers=headers,
            )

        assert response.status_code == 200, response.text
        assert len(calls) >= 2  # failed attempt + successful retry
        data = response.json()
        assert (data["status"], data["replacement_id"], data["miscmeta"]) == (
            ImageStatus.REPOST,
            original_id,
            "new info",
        )

        # Both histories recorded once, not once per attempt.
        rows = await history_rows(db_session, repost_id)
        assert [(r.old_value, r.new_value) for r in rows] == [("old info", "new info")]
        status_rows = await db_session.execute(
            select(ImageStatusHistory.old_status, ImageStatusHistory.new_status).where(
                ImageStatusHistory.image_id == repost_id
            )
        )
        assert [tuple(row) for row in status_rows] == [(ImageStatus.ACTIVE, ImageStatus.REPOST)]

        # The migration landed once: the tag and favourite moved to the
        # original and the repost keeps neither.
        moved_tags = await db_session.execute(
            select(func.count())
            .select_from(TagLinks)
            .where(TagLinks.image_id == original_id, TagLinks.tag_id == tag_id)
        )
        assert moved_tags.scalar_one() == 1
        leftover_tags = await db_session.execute(
            select(func.count()).select_from(TagLinks).where(TagLinks.image_id == repost_id)
        )
        assert leftover_tags.scalar_one() == 0
        moved_favs = await db_session.execute(
            select(func.count()).select_from(Favorites).where(Favorites.image_id == original_id)
        )
        assert moved_favs.scalar_one() == 1
