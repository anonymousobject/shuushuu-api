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
