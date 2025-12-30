"""
Tests for permission sync functionality.

Tests cover:
- Seeding missing permissions from enum to database
- Warning about orphan permissions in database
- Idempotent re-runs (no duplicate inserts)
"""

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.models.permissions import Perms


class TestSyncPermissions:
    """Test sync_permissions function."""

    async def test_inserts_missing_permissions(self, db_session: AsyncSession):
        """Permissions in enum but not in DB should be inserted."""
        from app.core.permission_sync import sync_permissions

        # Verify DB starts empty
        result = await db_session.execute(select(Perms))
        assert len(result.scalars().all()) == 0

        # Run sync
        await sync_permissions(db_session)

        # Verify all enum permissions were inserted
        result = await db_session.execute(select(Perms))
        db_perms = result.scalars().all()

        enum_titles = {p.value for p in Permission}
        db_titles = {p.title for p in db_perms}

        assert db_titles == enum_titles, f"Missing: {enum_titles - db_titles}"

    async def test_inserts_with_descriptions(self, db_session: AsyncSession):
        """Inserted permissions should have descriptions from enum."""
        from app.core.permission_sync import sync_permissions

        await sync_permissions(db_session)

        result = await db_session.execute(
            select(Perms).where(Perms.title == "tag_create")
        )
        perm = result.scalar_one()

        assert perm.desc == "Create new tags"

    async def test_warns_about_orphan_permissions(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ):
        """Permissions in DB but not in enum should trigger a warning."""
        from app.core.permission_sync import sync_permissions

        # Insert an orphan permission (not in enum)
        orphan = Perms(title="obsolete_permission", desc="No longer used")
        db_session.add(orphan)
        await db_session.commit()

        # Run sync with log capture
        with caplog.at_level(logging.WARNING):
            await sync_permissions(db_session)

        # Verify warning was logged
        assert any(
            "orphan_permission" in record.message and "obsolete_permission" in record.message
            for record in caplog.records
        ), f"Expected orphan warning, got: {[r.message for r in caplog.records]}"

    async def test_idempotent_rerun(self, db_session: AsyncSession):
        """Running sync twice should not create duplicate permissions."""
        from app.core.permission_sync import sync_permissions

        # First run
        await sync_permissions(db_session)
        result = await db_session.execute(select(Perms))
        count_after_first = len(result.scalars().all())

        # Second run
        await sync_permissions(db_session)
        result = await db_session.execute(select(Perms))
        count_after_second = len(result.scalars().all())

        assert count_after_first == count_after_second
