#!/usr/bin/env python3
"""
Audit script: find tags where an alias tag is used as a parent.

Reports two types of violations:
1. Tags whose parent (inheritedfrom_id) is an alias tag
2. Tags that are aliases AND have children inheriting from them

Usage: uv run python scripts/audit_alias_parent_violations.py
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.tag import Tags


async def audit() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, future=True
    )

    async with async_session() as db:
        # Violation 1: Tags whose parent is an alias
        parent_alias = Tags.__table__.alias("parent")
        result = await db.execute(
            select(
                Tags.tag_id,
                Tags.title,
                Tags.inheritedfrom_id,
                parent_alias.c.title.label("parent_title"),
                parent_alias.c.alias_of.label("parent_alias_of"),
            )
            .join(parent_alias, Tags.inheritedfrom_id == parent_alias.c.tag_id)
            .where(parent_alias.c.alias_of.isnot(None))
        )
        alias_parents = result.all()

        if alias_parents:
            print(f"\n=== Violation 1: {len(alias_parents)} tag(s) with alias parent ===")
            for row in alias_parents:
                print(
                    f"  Tag '{row.title}' (id: {row.tag_id}) "
                    f"has parent '{row.parent_title}' (id: {row.inheritedfrom_id}) "
                    f"which is alias of tag id {row.parent_alias_of}"
                )
        else:
            print("\n=== Violation 1: No tags with alias parents ===")

        # Violation 2: Tags that are aliases AND have children
        child_alias = Tags.__table__.alias("child")
        result = await db.execute(
            select(Tags.tag_id, Tags.title, Tags.alias_of)
            .where(Tags.alias_of.isnot(None))
            .where(
                Tags.tag_id.in_(
                    select(child_alias.c.inheritedfrom_id).where(
                        child_alias.c.inheritedfrom_id.isnot(None)
                    )
                )
            )
        )
        alias_with_children = result.all()

        if alias_with_children:
            print(
                f"\n=== Violation 2: {len(alias_with_children)} alias tag(s) that are parents ==="
            )
            for row in alias_with_children:
                children_result = await db.execute(
                    select(Tags.tag_id, Tags.title).where(Tags.inheritedfrom_id == row.tag_id)
                )
                children = children_result.all()
                child_list = ", ".join(f"'{t}' (id: {i})" for i, t in children)
                print(
                    f"  Tag '{row.title}' (id: {row.tag_id}) "
                    f"is alias of tag id {row.alias_of} "
                    f"but has children: {child_list}"
                )
        else:
            print("\n=== Violation 2: No alias tags with children ===")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(audit())
