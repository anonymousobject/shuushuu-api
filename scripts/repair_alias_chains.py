#!/usr/bin/env python3
"""
Repair script: fix chained and self-referential tag aliases.

Every alias resolver in the codebase is single-hop (see the invariant
enforced by validate_tag_relationships in app/api/v1/tags.py), but nothing
used to re-point a tag's INCOMING aliases when that tag itself got aliased
(see fix/alias-chain-reparent for the write-path fix). This is the one-off
repair for rows that went bad before that fix landed:

- Self-aliases (alias_of == tag_id, legacy rows): cleared to NULL.
- Chained aliases (X.alias_of points at a tag that is itself an alias):
  re-pointed straight at the terminal canonical tag.

Both are logged to tag_audit_log the same way the write-path fix logs a
reparent: ALIAS_REMOVED then ALIAS_SET (chains), or just ALIAS_REMOVED
(self-aliases). user_id is NULL -- these are system actions, not an admin's.

Self-aliases are processed first so one can never be mistaken for a live
chain terminal. A cycle (a chain that loops back on itself instead of
reaching a canonical tag) is reported and left untouched. An alias tag that
still carries tag_links (a separate invariant violation -- aliases should
carry no image links) is also reported and left untouched.

Usage:
    uv run python scripts/repair_alias_chains.py            # dry run (default)
    uv run python scripts/repair_alias_chains.py --apply     # write + audit
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import TagAuditActionType, settings
from app.models.tag import Tags
from app.models.tag_audit_log import TagAuditLog
from app.models.tag_link import TagLinks


def resolve_terminal(tag_id: int, alias_map: dict[int, int | None]) -> int | None:
    """Walk `alias_map` (tag_id -> alias_of, None if canonical) from `tag_id`
    to its terminal canonical tag.

    Returns None if the walk revisits a tag_id -- a cycle -- so callers can
    skip those rows instead of looping forever.
    """
    seen: set[int] = set()
    current = tag_id
    while current not in seen:
        seen.add(current)
        next_id = alias_map.get(current)
        if next_id is None:
            return current
        current = next_id
    return None


async def repair(*, apply: bool) -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, future=True)

    try:
        async with async_session() as db:
            rows = (await db.execute(select(Tags.tag_id, Tags.title, Tags.alias_of))).all()
            alias_map: dict[int, int | None] = {row.tag_id: row.alias_of for row in rows}
            titles: dict[int, str] = {row.tag_id: row.title for row in rows}

            self_alias_ids = [
                tag_id for tag_id, alias_of in alias_map.items() if alias_of == tag_id
            ]
            for tag_id in self_alias_ids:
                print(f"self-alias: '{titles[tag_id]}' (id: {tag_id}) -> NULL")
                if apply:
                    tag = (
                        await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
                    ).scalar_one()
                    db.add(
                        TagAuditLog(
                            tag_id=tag_id,
                            action_type=TagAuditActionType.ALIAS_REMOVED,
                            old_alias_of=tag_id,
                            new_alias_of=None,
                            user_id=None,
                        )
                    )
                    tag.alias_of = None
                    db.add(tag)
                # So the chain walk below can't treat this tag as a live alias.
                alias_map[tag_id] = None

            repaired_ids: list[int] = []
            for tag_id, alias_of in list(alias_map.items()):
                if alias_of is None:
                    continue
                # Only a genuine chain -- alias_of is itself an alias -- needs repair.
                if alias_map.get(alias_of) is None:
                    continue

                terminal = resolve_terminal(tag_id, alias_map)
                if terminal is None:
                    print(f"cycle detected starting at '{titles[tag_id]}' (id: {tag_id}), skipping")
                    continue

                links_count = (
                    await db.execute(
                        select(func.count()).select_from(TagLinks).where(TagLinks.tag_id == tag_id)  # type: ignore[arg-type]
                    )
                ).scalar()
                if links_count:
                    print(
                        f"WARNING: '{titles[tag_id]}' (id: {tag_id}) is an alias but still has "
                        f"{links_count} tag_links row(s), skipping"
                    )
                    continue

                print(
                    f"chain: '{titles[tag_id]}' (id: {tag_id}) -> {alias_of} -> ... -> {terminal}"
                )
                if apply:
                    tag = (
                        await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
                    ).scalar_one()
                    db.add(
                        TagAuditLog(
                            tag_id=tag_id,
                            action_type=TagAuditActionType.ALIAS_REMOVED,
                            old_alias_of=alias_of,
                            new_alias_of=None,
                            user_id=None,
                        )
                    )
                    db.add(
                        TagAuditLog(
                            tag_id=tag_id,
                            action_type=TagAuditActionType.ALIAS_SET,
                            old_alias_of=None,
                            new_alias_of=terminal,
                            user_id=None,
                        )
                    )
                    tag.alias_of = terminal
                    db.add(tag)
                    repaired_ids.append(tag_id)

            if not apply:
                print("\nDry run -- no changes written. Re-run with --apply to write.")
                return

            await db.commit()

            affected_ids = self_alias_ids + repaired_ids
            if not affected_ids:
                print("\nNo rows needed repair.")
                return

            print(f"\nRe-syncing {len(affected_ids)} affected tag(s) to Meilisearch...")
            try:
                from meilisearch_python_sdk import AsyncClient as MeilisearchClient

                from app.services.search import SearchService, sync_tag_to_search

                meili_client = MeilisearchClient(
                    url=settings.MEILISEARCH_URL, api_key=settings.MEILISEARCH_API_KEY
                )
                try:
                    search_service = SearchService(meili_client)
                    for tag_id in affected_ids:
                        tag = (
                            await db.execute(select(Tags).where(Tags.tag_id == tag_id))  # type: ignore[arg-type]
                        ).scalar_one()
                        await sync_tag_to_search(tag, db=db, service=search_service)
                    print("Meilisearch re-sync complete.")
                finally:
                    await meili_client.aclose()
            except Exception:
                print(
                    "WARNING: could not reach Meilisearch to re-sync affected tags. "
                    "Run `uv run python scripts/reindex_search.py` afterward."
                )
    finally:
        # Must run after the session above has released its connection back to
        # the pool -- disposing while `db` is still open orphans the raw
        # connection and raises "Event loop is closed" during GC.
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()
    asyncio.run(repair(apply=args.apply))


if __name__ == "__main__":
    main()
