"""
Tests for TagAuditLog functionality.

Tests that tag metadata changes (renames, type changes, alias changes, parent changes)
are properly logged to the tag_audit_log table.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TagAuditActionType, TagType
from app.core.security import get_password_hash
from app.models.permissions import Perms, UserPerms
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.user import Users


@pytest.mark.api
class TestTagAuditLogRename:
    """Tests for tag rename audit logging."""

    async def test_rename_tag_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that renaming a tag creates a TagAuditLog entry with RENAME action."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="auditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="auditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag to rename
        tag = Tags(title="old name", desc="test description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "auditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Rename tag
        update_data = {
            "title": "new name",
            "desc": "test description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(TagAuditLog.tag_id == tag.tag_id)
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.action_type == TagAuditActionType.RENAME
        assert audit_entry.old_title == "old name"
        assert audit_entry.new_title == "new name"
        assert audit_entry.user_id == admin.user_id

    async def test_rename_with_same_name_creates_no_audit_log(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that updating a tag without changing name creates no rename audit entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="auditadmin2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="auditadmin2@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="unchanged name", desc="old description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "auditadmin2", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Update tag without changing name (only the description). The description
        # change now emits its own DESCRIPTION_CHANGE entry; this test deliberately
        # asserts only that no RENAME entry is created.
        update_data = {
            "title": "unchanged name",
            "desc": "new description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify no RENAME audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.RENAME,
            )
        )
        audit_entries = audit_result.scalars().all()
        assert len(audit_entries) == 0


@pytest.mark.api
class TestTagAuditLogDescriptionChange:
    """Tests for tag description-change audit logging."""

    async def test_description_change_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Editing a tag's description creates a DESCRIPTION_CHANGE entry with old/new."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="descadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="descadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="stable name", desc="old description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "descadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        update_data = {
            "title": "stable name",
            "desc": "new description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.DESCRIPTION_CHANGE,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        entry = audit_entries[0]
        assert entry.old_desc == "old description"
        assert entry.new_desc == "new description"
        assert entry.user_id == admin.user_id

    async def test_same_description_creates_no_audit_log(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Updating a tag without changing its description logs no DESCRIPTION_CHANGE."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="descadmin2",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="descadmin2@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="old name", desc="same description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "descadmin2", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Change only the title; the description stays the same.
        update_data = {
            "title": "new name",
            "desc": "same description",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.DESCRIPTION_CHANGE,
            )
        )
        audit_entries = audit_result.scalars().all()
        assert len(audit_entries) == 0

    async def test_history_endpoint_returns_description_change(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /tags/{id}/history surfaces a description_change with old_desc/new_desc."""
        tag = Tags(title="some tag", desc="new description", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        audit = TagAuditLog(
            tag_id=tag.tag_id,
            action_type=TagAuditActionType.DESCRIPTION_CHANGE,
            old_desc="old description",
            new_desc="new description",
        )
        db_session.add(audit)
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        assert response.status_code == 200

        items = [i for i in response.json()["items"] if i["action_type"] == "description_change"]
        assert len(items) == 1
        assert items[0]["old_desc"] == "old description"
        assert items[0]["new_desc"] == "new description"

    async def test_adding_description_from_none_logs_old_desc_null(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Setting a description on a tag that had none logs old_desc=None → new."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="descadmin3",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="descadmin3@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        # Tag created with no description.
        tag = Tags(title="no desc tag", desc=None, type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "descadmin3", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        update_data = {
            "title": "no desc tag",
            "desc": "now it has one",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.DESCRIPTION_CHANGE,
            )
        )
        entries = audit_result.scalars().all()
        assert len(entries) == 1
        assert entries[0].old_desc is None
        assert entries[0].new_desc == "now it has one"

    async def test_empty_string_to_none_creates_no_audit_log(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Legacy tags store desc='' while the edit form sends null; that is not a change."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="descadmin4",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="descadmin4@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        # Legacy data: description is an empty string, not NULL.
        tag = Tags(title="legacy empty desc", desc="", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "descadmin4", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Edit something else; the form normalizes an empty description to null.
        update_data = {
            "title": "legacy empty desc renamed",
            "desc": None,
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.DESCRIPTION_CHANGE,
            )
        )
        audit_entries = audit_result.scalars().all()
        assert len(audit_entries) == 0

    async def test_clearing_description_stores_null_and_logs_new_desc_null(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Submitting desc='' clears the description to NULL, never storing ''."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username="descadmin5",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="descadmin5@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        user_perm = UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
        db_session.add(user_perm)
        await db_session.commit()

        tag = Tags(title="clearable desc", desc="about to be cleared", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "descadmin5", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        update_data = {
            "title": "clearable desc",
            "desc": "",
            "type": TagType.THEME,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert response.json()["desc"] is None

        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.DESCRIPTION_CHANGE,
            )
        )
        audit_entries = audit_result.scalars().all()
        assert len(audit_entries) == 1
        assert audit_entries[0].old_desc == "about to be cleared"
        assert audit_entries[0].new_desc is None


@pytest.mark.api
class TestTagAuditLogTypeChange:
    """Tests for tag type change audit logging."""

    async def test_type_change_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that changing a tag's type creates a TagAuditLog entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="typeauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="typeauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag with THEME type
        tag = Tags(title="type change tag", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "typeauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Change tag type to CHARACTER
        update_data = {
            "title": "type change tag",
            "desc": "test",
            "type": TagType.CHARACTER,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.TYPE_CHANGE,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.old_type == TagType.THEME
        assert audit_entry.new_type == TagType.CHARACTER
        assert audit_entry.user_id == admin.user_id


@pytest.mark.api
class TestTagAuditLogAliasChange:
    """Tests for tag alias change audit logging."""

    async def test_setting_alias_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that setting alias_of creates an ALIAS_SET audit entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="aliasauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="aliasauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create target tag (to be aliased to)
        target_tag = Tags(title="canonical tag", desc="target", type=TagType.THEME)
        db_session.add(target_tag)
        await db_session.commit()
        await db_session.refresh(target_tag)

        # Create tag that will become an alias
        alias_tag = Tags(title="alternate name", desc="will be alias", type=TagType.THEME)
        db_session.add(alias_tag)
        await db_session.commit()
        await db_session.refresh(alias_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "aliasauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set alias_of
        update_data = {
            "title": "alternate name",
            "desc": "will be alias",
            "type": TagType.THEME,
            "alias_of": target_tag.tag_id,
        }
        response = await client.put(
            f"/api/v1/tags/{alias_tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == alias_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.ALIAS_SET,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.old_alias_of is None
        assert audit_entry.new_alias_of == target_tag.tag_id
        assert audit_entry.user_id == admin.user_id

    async def test_removing_alias_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that removing alias_of creates an ALIAS_REMOVED audit entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="aliasremoveadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="aliasremoveadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create target tag
        target_tag = Tags(title="target for removal", desc="target", type=TagType.THEME)
        db_session.add(target_tag)
        await db_session.commit()
        await db_session.refresh(target_tag)

        # Create tag that already is an alias
        alias_tag = Tags(
            title="existing alias",
            desc="is alias",
            type=TagType.THEME,
            alias_of=target_tag.tag_id,
        )
        db_session.add(alias_tag)
        await db_session.commit()
        await db_session.refresh(alias_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "aliasremoveadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Remove alias_of (set to None)
        update_data = {
            "title": "existing alias",
            "desc": "is alias",
            "type": TagType.THEME,
            "alias_of": None,
        }
        response = await client.put(
            f"/api/v1/tags/{alias_tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == alias_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.ALIAS_REMOVED,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.old_alias_of == target_tag.tag_id
        assert audit_entry.new_alias_of is None
        assert audit_entry.user_id == admin.user_id


@pytest.mark.api
class TestTagAuditLogParentChange:
    """Tests for tag parent (inheritedfrom_id) change audit logging."""

    async def test_setting_parent_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that setting inheritedfrom_id creates a PARENT_SET audit entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="parentauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="parentauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create parent tag
        parent_tag = Tags(title="parent clothing", desc="parent", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tag (without parent yet)
        child_tag = Tags(title="swimsuit", desc="child", type=TagType.THEME)
        db_session.add(child_tag)
        await db_session.commit()
        await db_session.refresh(child_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "parentauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set parent
        update_data = {
            "title": "swimsuit",
            "desc": "child",
            "type": TagType.THEME,
            "inheritedfrom_id": parent_tag.tag_id,
        }
        response = await client.put(
            f"/api/v1/tags/{child_tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == child_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.PARENT_SET,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.old_parent_id is None
        assert audit_entry.new_parent_id == parent_tag.tag_id
        assert audit_entry.user_id == admin.user_id

    async def test_removing_parent_creates_audit_log_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that removing inheritedfrom_id creates a PARENT_REMOVED audit entry."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="parentremoveadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="parentremoveadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create parent tag
        parent_tag = Tags(title="old parent", desc="parent", type=TagType.THEME)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tag with parent
        child_tag = Tags(
            title="child with parent",
            desc="child",
            type=TagType.THEME,
            inheritedfrom_id=parent_tag.tag_id,
        )
        db_session.add(child_tag)
        await db_session.commit()
        await db_session.refresh(child_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "parentremoveadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Remove parent
        update_data = {
            "title": "child with parent",
            "desc": "child",
            "type": TagType.THEME,
            "inheritedfrom_id": None,
        }
        response = await client.put(
            f"/api/v1/tags/{child_tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify audit log entry was created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == child_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.PARENT_REMOVED,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.old_parent_id == parent_tag.tag_id
        assert audit_entry.new_parent_id is None
        assert audit_entry.user_id == admin.user_id


@pytest.mark.api
class TestTagAuditLogMultipleChanges:
    """Tests for multiple changes in a single update."""

    async def test_multiple_changes_create_multiple_audit_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that updating title AND type creates two separate audit entries."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="multiauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="multiauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="multi change old", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "multiauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Change both title AND type
        update_data = {
            "title": "multi change new",
            "desc": "test",
            "type": TagType.CHARACTER,
        }
        response = await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Verify two audit log entries were created
        audit_result = await db_session.execute(
            select(TagAuditLog).where(TagAuditLog.tag_id == tag.tag_id)
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 2

        # Check we have both types of entries
        action_types = {entry.action_type for entry in audit_entries}
        assert TagAuditActionType.RENAME in action_types
        assert TagAuditActionType.TYPE_CHANGE in action_types


@pytest.mark.api
class TestTagAuditLogOnCharacterSourceLinks:
    """Tests that TagAuditLog is written for character-source link changes."""

    async def test_create_link_creates_audit_entry(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Creating a character-source link should create an audit entry."""
        # Create TAG_CREATE permission (required for character-source link creation)
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="linkauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="linkauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create CHARACTER type tag
        character_tag = Tags(title="Sakura Kinomoto", desc="test character", type=TagType.CHARACTER)
        db_session.add(character_tag)
        await db_session.commit()
        await db_session.refresh(character_tag)

        # Create SOURCE type tag
        source_tag = Tags(title="Cardcaptor Sakura", desc="test source", type=TagType.SOURCE)
        db_session.add(source_tag)
        await db_session.commit()
        await db_session.refresh(source_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "linkauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create the character-source link
        response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 201

        # Verify TagAuditLog entry with action_type=SOURCE_LINKED
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == character_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.SOURCE_LINKED,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.character_tag_id == character_tag.tag_id
        assert audit_entry.source_tag_id == source_tag.tag_id
        assert audit_entry.user_id == admin.user_id

    async def test_delete_link_creates_audit_entry(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Deleting a character-source link should create an audit entry."""
        # Create TAG_CREATE permission (required for character-source link deletion)
        perm = Perms(title="tag_create", desc="Create tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="linkdelauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="linkdelauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_CREATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create CHARACTER type tag
        character_tag = Tags(title="Tomoyo Daidouji", desc="test character", type=TagType.CHARACTER)
        db_session.add(character_tag)
        await db_session.commit()
        await db_session.refresh(character_tag)

        # Create SOURCE type tag
        source_tag = Tags(title="Cardcaptor Sakura", desc="test source", type=TagType.SOURCE)
        db_session.add(source_tag)
        await db_session.commit()
        await db_session.refresh(source_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "linkdelauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Create the character-source link first
        create_response = await client.post(
            "/api/v1/character-source-links",
            json={
                "character_tag_id": character_tag.tag_id,
                "source_tag_id": source_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert create_response.status_code == 201
        link_id = create_response.json()["id"]

        # Delete the link
        delete_response = await client.delete(
            f"/api/v1/character-source-links/{link_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert delete_response.status_code == 204

        # Verify TagAuditLog entry with action_type=SOURCE_UNLINKED
        audit_result = await db_session.execute(
            select(TagAuditLog).where(
                TagAuditLog.tag_id == character_tag.tag_id,
                TagAuditLog.action_type == TagAuditActionType.SOURCE_UNLINKED,
            )
        )
        audit_entries = audit_result.scalars().all()

        assert len(audit_entries) == 1
        audit_entry = audit_entries[0]
        assert audit_entry.character_tag_id == character_tag.tag_id
        assert audit_entry.source_tag_id == source_tag.tag_id
        assert audit_entry.user_id == admin.user_id


@pytest.mark.api
class TestGetTagHistory:
    """Tests for GET /tags/{tag_id}/history endpoint."""

    async def test_get_tag_history_returns_audit_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /tags/{tag_id}/history should return audit entries."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="historyauditadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="historyauditadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag to rename (this will generate audit history)
        tag = Tags(title="history test old name", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "historyauditadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Rename tag to generate audit history
        update_data = {
            "title": "history test new name",
            "desc": "test",
            "type": TagType.THEME,
        }
        await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # GET the tag history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "items" in data
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

        # Verify the rename entry is present
        rename_entry = next(
            (item for item in data["items"] if item["action_type"] == TagAuditActionType.RENAME),
            None,
        )
        assert rename_entry is not None
        assert rename_entry["old_title"] == "history test old name"
        assert rename_entry["new_title"] == "history test new name"

    async def test_get_tag_history_includes_user_info(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History entries should include user info."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="historyuseradmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="historyuseradmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="user info tag", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "historyuseradmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Rename tag
        await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json={"title": "user info tag renamed", "desc": "test", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # GET the tag history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) >= 1

        # Verify user info is present
        entry = data["items"][0]
        assert "user" in entry
        assert entry["user"] is not None
        assert entry["user"]["user_id"] == admin.user_id
        assert entry["user"]["username"] == "historyuseradmin"

    async def test_get_tag_history_404_for_nonexistent_tag(
        self, client: AsyncClient
    ) -> None:
        """Should return 404 for nonexistent tag."""
        response = await client.get("/api/v1/tags/99999999/history")
        assert response.status_code == 404

    async def test_get_tag_history_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Tag history should support pagination."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="historypageadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="historypageadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag and make multiple changes
        tag = Tags(title="pagination tag v1", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "historypageadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Make multiple updates to create multiple audit entries
        for i in range(3):
            await client.put(
                f"/api/v1/tags/{tag.tag_id}",
                json={"title": f"pagination tag v{i+2}", "desc": "test", "type": TagType.THEME},
                headers={"Authorization": f"Bearer {access_token}"},
            )

        # Get first page with per_page=2
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}/history?page=1&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 2
        assert len(data["items"]) == 2
        assert data["total"] >= 3

        # Get second page
        response = await client.get(
            f"/api/v1/tags/{tag.tag_id}/history?page=2&per_page=2"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert len(data["items"]) >= 1

    async def test_get_tag_history_ordered_by_most_recent(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History should be ordered by most recent first."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="historyorderadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="historyorderadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create tag
        tag = Tags(title="order tag first", desc="test", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "historyorderadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Make multiple changes
        await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json={"title": "order tag second", "desc": "test", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        await client.put(
            f"/api/v1/tags/{tag.tag_id}",
            json={"title": "order tag third", "desc": "test", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # Get history
        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        assert response.status_code == 200
        data = response.json()

        # Most recent should be first (third rename)
        assert data["items"][0]["new_title"] == "order tag third"
        assert data["items"][1]["new_title"] == "order tag second"

    async def test_get_tag_history_includes_alias_tag_info(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that alias changes include resolved alias_tag info."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="aliastaginfoadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="aliastaginfoadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create target tag (the one that will be aliased to)
        target_tag = Tags(title="Target Tag", desc="target", type=TagType.CHARACTER)
        db_session.add(target_tag)
        await db_session.commit()
        await db_session.refresh(target_tag)

        # Create source tag (the one that will become an alias)
        source_tag = Tags(title="Source Alias", desc="source", type=TagType.CHARACTER)
        db_session.add(source_tag)
        await db_session.commit()
        await db_session.refresh(source_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "aliastaginfoadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set alias
        response = await client.put(
            f"/api/v1/tags/{source_tag.tag_id}",
            json={
                "title": "Source Alias",
                "desc": "source",
                "type": TagType.CHARACTER,
                "alias_of": target_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Get history
        response = await client.get(f"/api/v1/tags/{source_tag.tag_id}/history")
        assert response.status_code == 200
        data = response.json()

        assert len(data["items"]) >= 1
        alias_entry = data["items"][0]
        assert alias_entry["action_type"] == "alias_set"
        assert alias_entry["new_alias_of"] == target_tag.tag_id
        # Verify alias_tag is populated with full tag info
        assert alias_entry["alias_tag"] is not None
        assert alias_entry["alias_tag"]["tag_id"] == target_tag.tag_id
        assert alias_entry["alias_tag"]["title"] == "Target Tag"
        assert alias_entry["alias_tag"]["type"] == TagType.CHARACTER

    async def test_get_tag_history_includes_parent_tag_info(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Test that parent changes include resolved parent_tag info."""
        # Create TAG_UPDATE permission
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        # Create admin user
        admin = Users(
            username="parenttaginfoadmin",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="parenttaginfoadmin@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        # Grant TAG_UPDATE permission
        user_perm = UserPerms(
            user_id=admin.user_id,
            perm_id=perm.perm_id,
            permvalue=1,
        )
        db_session.add(user_perm)
        await db_session.commit()

        # Create parent tag
        parent_tag = Tags(title="Parent Source", desc="parent", type=TagType.SOURCE)
        db_session.add(parent_tag)
        await db_session.commit()
        await db_session.refresh(parent_tag)

        # Create child tag
        child_tag = Tags(title="Child Source", desc="child", type=TagType.SOURCE)
        db_session.add(child_tag)
        await db_session.commit()
        await db_session.refresh(child_tag)

        # Login as admin
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"username": "parenttaginfoadmin", "password": "AdminPassword123!"},
        )
        access_token = login_response.json()["access_token"]

        # Set parent
        response = await client.put(
            f"/api/v1/tags/{child_tag.tag_id}",
            json={
                "title": "Child Source",
                "desc": "child",
                "type": TagType.SOURCE,
                "inheritedfrom_id": parent_tag.tag_id,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Get history
        response = await client.get(f"/api/v1/tags/{child_tag.tag_id}/history")
        assert response.status_code == 200
        data = response.json()

        assert len(data["items"]) >= 1
        parent_entry = data["items"][0]
        assert parent_entry["action_type"] == "parent_set"
        assert parent_entry["new_parent_id"] == parent_tag.tag_id
        # Verify parent_tag is populated with full tag info
        assert parent_entry["parent_tag"] is not None
        assert parent_entry["parent_tag"]["tag_id"] == parent_tag.tag_id
        assert parent_entry["parent_tag"]["title"] == "Parent Source"
        assert parent_entry["parent_tag"]["type"] == TagType.SOURCE


@pytest.mark.api
class TestTagAuditLogExternalLinks:
    """Tests for external-link audit logging."""

    async def _admin_and_token(
        self, client: AsyncClient, db_session: AsyncSession, username: str
    ) -> tuple[Users, str]:
        """Create a TAG_UPDATE admin and return them with a bearer token."""
        perm = Perms(title="tag_update", desc="Update tags")
        db_session.add(perm)
        await db_session.commit()
        await db_session.refresh(perm)

        admin = Users(
            username=username,
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email=f"{username}@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        db_session.add(UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1))
        await db_session.commit()

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "AdminPassword123!"},
        )
        return admin, login.json()["access_token"]

    async def _tag(self, db_session: AsyncSession, title: str) -> Tags:
        tag = Tags(title=title, type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)
        return tag

    async def test_add_link_creates_link_added_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        admin, token = await self._admin_and_token(client, db_session, "linkaudit1")
        tag = await self._tag(db_session, "link audit add")

        response = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json={"url": "https://example.com/artist"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == tag.tag_id)
            )
        ).scalars().all()

        assert len(entries) == 1
        assert entries[0].action_type == TagAuditActionType.LINK_ADDED
        assert entries[0].link_url == "https://example.com/artist"
        assert entries[0].user_id == admin.user_id

    @pytest.mark.needs_commit
    async def test_duplicate_link_creates_no_audit_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """The 409 path rolls back; the audit row must roll back with it.

        needs_commit: the duplicate-URL path performs a real session rollback;
        under the default SAVEPOINT isolation that rollback would unwind the
        first (successful) link_added row too, which can't happen in
        production where it is durably committed before the second request.
        """
        _, token = await self._admin_and_token(client, db_session, "linkaudit2")
        tag = await self._tag(db_session, "link audit dupe")
        # Captured before the request that rolls back: the rollback expires every
        # object loaded in this session (it's shared with the app via the get_db
        # override), and a bare `tag.tag_id` access afterward would try to
        # lazy-refresh the expired attribute outside of a greenlet context.
        tag_id = tag.tag_id
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"url": "https://example.com/dupe"}

        assert (
            await client.post(f"/api/v1/tags/{tag_id}/links", json=payload, headers=headers)
        ).status_code == 201
        assert (
            await client.post(f"/api/v1/tags/{tag_id}/links", json=payload, headers=headers)
        ).status_code == 409

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == tag_id)
            )
        ).scalars().all()
        assert len(entries) == 1

    async def test_delete_link_creates_link_removed_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        admin, token = await self._admin_and_token(client, db_session, "linkaudit3")
        tag = await self._tag(db_session, "link audit delete")
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json={"url": "https://example.com/gone"},
            headers=headers,
        )
        link_id = created.json()["link_id"]

        response = await client.delete(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}", headers=headers
        )
        assert response.status_code == 204

        entries = (
            await db_session.execute(
                select(TagAuditLog)
                .where(TagAuditLog.tag_id == tag.tag_id)
                .where(TagAuditLog.action_type == TagAuditActionType.LINK_REMOVED)
            )
        ).scalars().all()

        assert len(entries) == 1
        assert entries[0].link_url == "https://example.com/gone"
        assert entries[0].user_id == admin.user_id

    async def test_reorder_links_creates_no_audit_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Display order is cosmetic; auditing it would be history noise.
        _, token = await self._admin_and_token(client, db_session, "linkaudit4")
        tag = await self._tag(db_session, "link audit reorder")
        headers = {"Authorization": f"Bearer {token}"}

        ids = []
        for suffix in ("one", "two"):
            created = await client.post(
                f"/api/v1/tags/{tag.tag_id}/links",
                json={"url": f"https://example.com/{suffix}"},
                headers=headers,
            )
            ids.append(created.json()["link_id"])

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/reorder",
            json={"link_ids": list(reversed(ids))},
            headers=headers,
        )
        assert response.status_code == 200

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == tag.tag_id)
            )
        ).scalars().all()
        # Only the two link_added rows from setup.
        assert [e.action_type for e in entries] == [TagAuditActionType.LINK_ADDED] * 2

    async def _tag_with_link(
        self, client: AsyncClient, db_session: AsyncSession, title: str, token: str
    ) -> tuple[Tags, int]:
        tag = await self._tag(db_session, title)
        created = await client.post(
            f"/api/v1/tags/{tag.tag_id}/links",
            json={"url": "https://example.com/subject"},
            headers={"Authorization": f"Bearer {token}"},
        )
        return tag, created.json()["link_id"]

    async def _link_entries(self, db_session: AsyncSession, tag_id: int, action: str):
        return (
            await db_session.execute(
                select(TagAuditLog)
                .where(TagAuditLog.tag_id == tag_id)
                .where(TagAuditLog.action_type == action)
                .order_by(TagAuditLog.id)
            )
        ).scalars().all()

    async def test_marking_link_dead_creates_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        admin, token = await self._admin_and_token(client, db_session, "linkaudit5")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "dead mark", token)

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"is_dead": True},
            headers=headers,
        )
        assert response.status_code == 200

        entries = await self._link_entries(
            db_session, tag.tag_id, TagAuditActionType.LINK_DEAD_MARKED
        )
        assert len(entries) == 1
        assert entries[0].link_url == "https://example.com/subject"
        assert entries[0].user_id == admin.user_id

    async def test_clearing_dead_flag_creates_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        _, token = await self._admin_and_token(client, db_session, "linkaudit6")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "dead clear", token)

        await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"is_dead": True},
            headers=headers,
        )
        await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"is_dead": False},
            headers=headers,
        )

        entries = await self._link_entries(
            db_session, tag.tag_id, TagAuditActionType.LINK_DEAD_CLEARED
        )
        assert len(entries) == 1
        assert entries[0].link_url == "https://example.com/subject"

    async def test_redundant_dead_flag_creates_no_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Clearing a flag that was never set changes nothing, so it logs nothing.
        # Mirrors test_rename_with_same_name_creates_no_audit_log.
        _, token = await self._admin_and_token(client, db_session, "linkaudit7")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "dead noop", token)

        await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"is_dead": False},
            headers=headers,
        )

        assert (
            await self._link_entries(
                db_session, tag.tag_id, TagAuditActionType.LINK_DEAD_CLEARED
            )
            == []
        )

    async def test_archive_url_change_records_old_and_new(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        _, token = await self._admin_and_token(client, db_session, "linkaudit8")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "archive set", token)

        await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"archive_url": "https://web.archive.org/first"},
            headers=headers,
        )
        await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"archive_url": "https://web.archive.org/second"},
            headers=headers,
        )

        entries = await self._link_entries(
            db_session, tag.tag_id, TagAuditActionType.LINK_ARCHIVE_CHANGED
        )
        assert len(entries) == 2
        assert entries[0].old_archive_url is None
        assert entries[0].new_archive_url == "https://web.archive.org/first"
        assert entries[1].old_archive_url == "https://web.archive.org/first"
        assert entries[1].new_archive_url == "https://web.archive.org/second"
        assert all(e.link_url == "https://example.com/subject" for e in entries)

    async def test_unchanged_archive_url_creates_no_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        _, token = await self._admin_and_token(client, db_session, "linkaudit9")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "archive noop", token)

        for _ in range(2):
            await client.patch(
                f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
                json={"archive_url": "https://web.archive.org/same"},
                headers=headers,
            )

        entries = await self._link_entries(
            db_session, tag.tag_id, TagAuditActionType.LINK_ARCHIVE_CHANGED
        )
        assert len(entries) == 1

    async def test_dead_and_archive_in_one_call_create_two_entries(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # The two are independent inputs on one endpoint; collapsing them into a
        # single row would lose one of the two facts.
        _, token = await self._admin_and_token(client, db_session, "linkaudit10")
        headers = {"Authorization": f"Bearer {token}"}
        tag, link_id = await self._tag_with_link(client, db_session, "dead plus archive", token)

        response = await client.patch(
            f"/api/v1/tags/{tag.tag_id}/links/{link_id}",
            json={"is_dead": True, "archive_url": "https://web.archive.org/both"},
            headers=headers,
        )
        assert response.status_code == 200

        assert (
            len(
                await self._link_entries(
                    db_session, tag.tag_id, TagAuditActionType.LINK_DEAD_MARKED
                )
            )
            == 1
        )
        archive = await self._link_entries(
            db_session, tag.tag_id, TagAuditActionType.LINK_ARCHIVE_CHANGED
        )
        assert len(archive) == 1
        assert archive[0].new_archive_url == "https://web.archive.org/both"

    async def test_alias_migration_audits_moved_and_deleted_links(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Setting an alias silently moves the alias's external links onto the
        # canonical tag and drops URL collisions. Without these rows a link
        # appears on the canonical tag with no history explaining how.
        _, token = await self._admin_and_token(client, db_session, "linkaudit11")
        headers = {"Authorization": f"Bearer {token}"}

        canonical = await self._tag(db_session, "alias migration canonical")
        alias = await self._tag(db_session, "alias migration alias")

        shared = "https://example.com/shared"
        unique = "https://example.com/unique"

        for tag_id, url in ((canonical.tag_id, shared), (alias.tag_id, shared), (alias.tag_id, unique)):
            created = await client.post(
                f"/api/v1/tags/{tag_id}/links", json={"url": url}, headers=headers
            )
            assert created.status_code == 201

        response = await client.put(
            f"/api/v1/tags/{alias.tag_id}",
            json={
                "title": "alias migration alias",
                "type": TagType.THEME,
                "alias_of": canonical.tag_id,
            },
            headers=headers,
        )
        assert response.status_code == 200

        removed = await self._link_entries(
            db_session, alias.tag_id, TagAuditActionType.LINK_REMOVED
        )
        # Both of the alias's links leave it: one deleted as a collision, one moved.
        assert sorted(e.link_url for e in removed) == [shared, unique]

        added = await self._link_entries(
            db_session, canonical.tag_id, TagAuditActionType.LINK_ADDED
        )
        # The canonical tag's own setup link, plus the one that moved. The
        # colliding URL must NOT be added again.
        assert sorted(e.link_url for e in added) == [shared, unique]


@pytest.mark.api
class TestTagAuditLogCreation:
    """A tag born as an alias or child must have that recorded."""

    async def _admin_token(self, client: AsyncClient, db_session: AsyncSession, username: str) -> str:
        for title, desc in (("tag_create", "Create tags"), ("tag_update", "Update tags")):
            db_session.add(Perms(title=title, desc=desc))
        await db_session.commit()

        admin = Users(
            username=username,
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email=f"{username}@example.com",
            active=1,
            admin=1,
        )
        db_session.add(admin)
        await db_session.commit()
        await db_session.refresh(admin)

        perms = (await db_session.execute(select(Perms))).scalars().all()
        for perm in perms:
            db_session.add(
                UserPerms(user_id=admin.user_id, perm_id=perm.perm_id, permvalue=1)
            )
        await db_session.commit()

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "AdminPassword123!"},
        )
        return login.json()["access_token"]

    async def test_creating_a_tag_as_an_alias_logs_alias_set(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._admin_token(client, db_session, "createaudit1")
        target = Tags(title="create audit target", type=TagType.THEME)
        db_session.add(target)
        await db_session.commit()
        await db_session.refresh(target)

        response = await client.post(
            "/api/v1/tags",
            json={
                "title": "create audit alias",
                "type": TagType.THEME,
                "alias_of": target.tag_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        new_id = response.json()["tag_id"]

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == new_id)
            )
        ).scalars().all()

        assert len(entries) == 1
        assert entries[0].action_type == TagAuditActionType.ALIAS_SET
        assert entries[0].old_alias_of is None
        assert entries[0].new_alias_of == target.tag_id

    async def test_creating_a_tag_with_a_parent_logs_parent_set(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._admin_token(client, db_session, "createaudit2")
        parent = Tags(title="create audit parent", type=TagType.THEME)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(parent)

        response = await client.post(
            "/api/v1/tags",
            json={
                "title": "create audit child",
                "type": TagType.THEME,
                "inheritedfrom_id": parent.tag_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        new_id = response.json()["tag_id"]

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == new_id)
            )
        ).scalars().all()

        assert len(entries) == 1
        assert entries[0].action_type == TagAuditActionType.PARENT_SET
        assert entries[0].old_parent_id is None
        assert entries[0].new_parent_id == parent.tag_id

    async def test_creating_a_tag_with_both_alias_and_parent_logs_both(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._admin_token(client, db_session, "createaudit4")
        target = Tags(title="create audit both target", type=TagType.THEME)
        parent = Tags(title="create audit both parent", type=TagType.THEME)
        db_session.add(target)
        db_session.add(parent)
        await db_session.commit()
        await db_session.refresh(target)
        await db_session.refresh(parent)

        response = await client.post(
            "/api/v1/tags",
            json={
                "title": "create audit both",
                "type": TagType.THEME,
                "alias_of": target.tag_id,
                "inheritedfrom_id": parent.tag_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        new_id = response.json()["tag_id"]

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == new_id)
            )
        ).scalars().all()

        assert len(entries) == 2
        by_action = {entry.action_type: entry for entry in entries}
        assert by_action[TagAuditActionType.ALIAS_SET].old_alias_of is None
        assert by_action[TagAuditActionType.ALIAS_SET].new_alias_of == target.tag_id
        assert by_action[TagAuditActionType.PARENT_SET].old_parent_id is None
        assert by_action[TagAuditActionType.PARENT_SET].new_parent_id == parent.tag_id

    async def test_creating_a_plain_tag_logs_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._admin_token(client, db_session, "createaudit3")

        response = await client.post(
            "/api/v1/tags",
            json={"title": "create audit plain", "type": TagType.THEME},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        entries = (
            await db_session.execute(
                select(TagAuditLog).where(TagAuditLog.tag_id == response.json()["tag_id"])
            )
        ).scalars().all()
        assert entries == []


@pytest.mark.api
class TestTagHistoryIncomingRelations:
    """A target tag's history must show relationships pointed at it."""

    async def test_canonical_tag_sees_incoming_alias(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # The alias_set row is stored against the ALIAS tag, so the canonical tag
        # never saw that something was aliased to it.
        canonical = Tags(title="incoming canonical", type=TagType.THEME)
        alias = Tags(title="incoming alias", type=TagType.THEME)
        db_session.add_all([canonical, alias])
        await db_session.commit()
        await db_session.refresh(canonical)
        await db_session.refresh(alias)

        db_session.add(
            TagAuditLog(
                tag_id=alias.tag_id,
                action_type=TagAuditActionType.ALIAS_SET,
                new_alias_of=canonical.tag_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{canonical.tag_id}/history")
        assert response.status_code == 200
        items = response.json()["items"]

        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.ALIAS_SET
        assert items[0]["tag_id"] == alias.tag_id
        # subject_tag names the tag the row is ABOUT, so the frontend can render
        # "Alias added: <alias>" instead of a badge pointing back at itself.
        assert items[0]["subject_tag"]["tag_id"] == alias.tag_id
        assert items[0]["subject_tag"]["title"] == "incoming alias"

    async def test_parent_tag_sees_incoming_child(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        parent = Tags(title="incoming parent", type=TagType.THEME)
        child = Tags(title="incoming child", type=TagType.THEME)
        db_session.add_all([parent, child])
        await db_session.commit()
        await db_session.refresh(parent)
        await db_session.refresh(child)

        db_session.add(
            TagAuditLog(
                tag_id=child.tag_id,
                action_type=TagAuditActionType.PARENT_SET,
                new_parent_id=parent.tag_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{parent.tag_id}/history")
        items = response.json()["items"]

        assert len(items) == 1
        assert items[0]["subject_tag"]["tag_id"] == child.tag_id

    async def test_own_history_still_returns_its_own_rows(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Regression guard: broadening the WHERE must not drop the original rows.
        tag = Tags(title="own rows", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                action_type=TagAuditActionType.RENAME,
                old_title="before",
                new_title="own rows",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        items = response.json()["items"]

        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.RENAME
        assert items[0]["subject_tag"]["tag_id"] == tag.tag_id

    async def test_link_fields_are_exposed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tag = Tags(title="link fields exposed", type=TagType.THEME)
        db_session.add(tag)
        await db_session.commit()
        await db_session.refresh(tag)

        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                action_type=TagAuditActionType.LINK_ARCHIVE_CHANGED,
                link_url="https://example.com/subject",
                old_archive_url=None,
                new_archive_url="https://web.archive.org/x",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{tag.tag_id}/history")
        item = response.json()["items"][0]

        assert item["link_url"] == "https://example.com/subject"
        assert item["old_archive_url"] is None
        assert item["new_archive_url"] == "https://web.archive.org/x"

    async def test_canonical_tag_sees_incoming_removed_alias(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # A tag that a *removed* alias used to point at must still see the
        # alias_removed row, via the old_alias_of disjunct.
        canonical = Tags(title="incoming canonical removed", type=TagType.THEME)
        former_alias = Tags(title="incoming former alias", type=TagType.THEME)
        db_session.add_all([canonical, former_alias])
        await db_session.commit()
        await db_session.refresh(canonical)
        await db_session.refresh(former_alias)

        db_session.add(
            TagAuditLog(
                tag_id=former_alias.tag_id,
                action_type=TagAuditActionType.ALIAS_REMOVED,
                old_alias_of=canonical.tag_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{canonical.tag_id}/history")
        assert response.status_code == 200
        items = response.json()["items"]

        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.ALIAS_REMOVED
        assert items[0]["subject_tag"]["tag_id"] == former_alias.tag_id

    async def test_parent_tag_sees_incoming_removed_child(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        # Same as above, but for a removed parent link via old_parent_id.
        parent = Tags(title="incoming parent removed", type=TagType.THEME)
        former_child = Tags(title="incoming former child", type=TagType.THEME)
        db_session.add_all([parent, former_child])
        await db_session.commit()
        await db_session.refresh(parent)
        await db_session.refresh(former_child)

        db_session.add(
            TagAuditLog(
                tag_id=former_child.tag_id,
                action_type=TagAuditActionType.PARENT_REMOVED,
                old_parent_id=parent.tag_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/tags/{parent.tag_id}/history")
        assert response.status_code == 200
        items = response.json()["items"]

        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.PARENT_REMOVED
        assert items[0]["subject_tag"]["tag_id"] == former_child.tag_id


@pytest.mark.api
class TestUserHistoryLinkEvents:
    """Link rows reach the user activity feed whether or not we plan for it."""

    async def test_user_history_exposes_link_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        actor = Users(
            username="feedlinkuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="feedlinkuser@example.com",
            active=1,
        )
        tag = Tags(title="feed link tag", type=TagType.THEME)
        db_session.add_all([actor, tag])
        await db_session.commit()
        await db_session.refresh(actor)
        await db_session.refresh(tag)

        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                action_type=TagAuditActionType.LINK_ADDED,
                link_url="https://example.com/feed",
                user_id=actor.user_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{actor.user_id}/history")
        assert response.status_code == 200

        items = [i for i in response.json()["items"] if i["type"] == "tag_metadata"]
        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.LINK_ADDED
        assert items[0]["link_url"] == "https://example.com/feed"
        assert items[0]["tag"]["tag_id"] == tag.tag_id

    async def test_user_history_exposes_archive_url_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A link_archive_changed row must survive with link_url AND both
        archive-url sides intact — the handler passes all three through
        together, but the sibling link-fields test above only asserts
        link_url, so losing old_archive_url/new_archive_url would render as
        silently wrong data rather than a caught error."""
        actor = Users(
            username="feedarchiveuser",
            password=get_password_hash("AdminPassword123!"),
            password_type="bcrypt",
            salt="",
            email="feedarchiveuser@example.com",
            active=1,
        )
        tag = Tags(title="feed archive tag", type=TagType.THEME)
        db_session.add_all([actor, tag])
        await db_session.commit()
        await db_session.refresh(actor)
        await db_session.refresh(tag)

        db_session.add(
            TagAuditLog(
                tag_id=tag.tag_id,
                action_type=TagAuditActionType.LINK_ARCHIVE_CHANGED,
                link_url="https://example.com/feed",
                old_archive_url=None,
                new_archive_url="https://web.archive.org/feed",
                user_id=actor.user_id,
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/v1/users/{actor.user_id}/history")
        assert response.status_code == 200

        items = [i for i in response.json()["items"] if i["type"] == "tag_metadata"]
        assert len(items) == 1
        assert items[0]["action_type"] == TagAuditActionType.LINK_ARCHIVE_CHANGED
        assert items[0]["link_url"] == "https://example.com/feed"
        assert items[0]["old_archive_url"] is None
        assert items[0]["new_archive_url"] == "https://web.archive.org/feed"
        assert items[0]["tag"]["tag_id"] == tag.tag_id
