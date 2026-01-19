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

        # Update tag without changing name (only description)
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
