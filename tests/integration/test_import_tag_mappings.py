"""Tests for scripts/import_tag_mappings.py — resolving CSV rows to tag_mappings.

These guard the title -> internal-tag-id resolution, which must work for BOTH
theme (type 1) and character (type 4) internal tags. The original importer only
loaded theme tags, so every character mapping silently failed to import.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tags
from app.models.tag_mapping import TagMappings
from app.models.user import Users
from scripts.import_tag_mappings import import_mappings


async def _make_user(db: AsyncSession) -> Users:
    user = Users(
        username="import_mappings_test_user",
        email="importmappings@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
        active=1,
    )
    db.add(user)
    await db.flush()
    return user


def _write_csv(tmp_path: Path, rows: list[tuple[str, str, str]]) -> Path:
    csv_path = tmp_path / "tag_mappings.csv"
    lines = ["danbooru_tag,internal_tag_title,action"]
    lines += [f"{d},{t},{a}" for d, t, a in rows]
    csv_path.write_text("\n".join(lines) + "\n")
    return csv_path


async def test_import_maps_character_tag(db_session: AsyncSession, tmp_path: Path) -> None:
    """A character (type=4) internal tag must be resolvable by title and mapped."""
    user = await _make_user(db_session)
    char = Tags(title="Zzz Test Character", type=4, user_id=user.user_id)
    db_session.add(char)
    await db_session.flush()

    csv_path = _write_csv(tmp_path, [("zzz_test_char", "Zzz Test Character", "map")])
    summary = await import_mappings(db_session, csv_path)

    row = (
        await db_session.execute(
            select(TagMappings).where(TagMappings.external_tag == "zzz_test_char")
        )
    ).scalar_one_or_none()
    assert row is not None, "character mapping should have been created"
    assert row.internal_tag_id == char.tag_id
    assert summary["created"] == 1
    assert summary["errors"] == []


async def test_import_maps_theme_tag(db_session: AsyncSession, tmp_path: Path) -> None:
    """Regression: theme (type=1) tags still map after broadening the lookup."""
    user = await _make_user(db_session)
    theme = Tags(title="Zzz Test Theme", type=1, user_id=user.user_id)
    db_session.add(theme)
    await db_session.flush()

    csv_path = _write_csv(tmp_path, [("zzz_test_theme", "Zzz Test Theme", "map")])
    summary = await import_mappings(db_session, csv_path)

    row = (
        await db_session.execute(
            select(TagMappings).where(TagMappings.external_tag == "zzz_test_theme")
        )
    ).scalar_one()
    assert row.internal_tag_id == theme.tag_id
    assert summary["created"] == 1


async def test_import_skips_existing_external_tag(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Insert-only: an external_tag already present is skipped, not duplicated/updated."""
    user = await _make_user(db_session)
    theme = Tags(title="Zzz Existing Theme", type=1, user_id=user.user_id)
    db_session.add(theme)
    await db_session.flush()
    db_session.add(
        TagMappings(external_tag="zzz_existing", internal_tag_id=theme.tag_id, confidence=1.0)
    )
    await db_session.flush()

    csv_path = _write_csv(tmp_path, [("zzz_existing", "Zzz Existing Theme", "map")])
    summary = await import_mappings(db_session, csv_path)

    assert summary["created"] == 0
    assert summary["skipped"] == 1
    rows = (
        await db_session.execute(
            select(TagMappings).where(TagMappings.external_tag == "zzz_existing")
        )
    ).scalars().all()
    assert len(rows) == 1  # not duplicated


async def test_import_ambiguous_title_is_reported_not_mapped(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """If a theme and a character tag share a title, the title is ambiguous and the
    row is reported as an error rather than silently mapped to an arbitrary one."""
    user = await _make_user(db_session)
    db_session.add_all(
        [
            Tags(title="Zzz Ambiguous", type=1, user_id=user.user_id),
            Tags(title="Zzz Ambiguous", type=4, user_id=user.user_id),
        ]
    )
    await db_session.flush()

    csv_path = _write_csv(tmp_path, [("zzz_ambiguous", "Zzz Ambiguous", "map")])
    summary = await import_mappings(db_session, csv_path)

    assert summary["created"] == 0
    errors = summary["errors"]
    assert isinstance(errors, list) and len(errors) == 1
    assert "Ambiguous" in errors[0]
    row = (
        await db_session.execute(
            select(TagMappings).where(TagMappings.external_tag == "zzz_ambiguous")
        )
    ).scalar_one_or_none()
    assert row is None  # not mapped
