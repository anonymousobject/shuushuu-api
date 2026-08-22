"""MariaDB -> Postgres data migration orchestrator.

The executable half of docs/postgres-cutover-runbook.md — read that first.
Subcommands mirror the runbook sequence and are all idempotent:

    preflight   target schema at chain head, no other clients, source clean
    prep        temp tw_tagid column + truncate target tables
    load        pgloader (docker) with the settings that survived rehearsal
    post        normalize FKs to model names, sequences, ANALYZE
    validate    per-table count comparison + FK/sequence sanity (exit 1 on diff)
    all         everything above, in order

Environment:
    SOURCE_MYSQL_URL   mysql://user:pass@host:port/db      (pgloader form)
    TARGET_PG_URL      postgresql://user:pass@host:port/db (pgloader form)

Nothing may write to either database while this runs; preflight enforces the
target side and the runbook's freeze step covers the source.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

SOURCE_URL = os.environ.get("SOURCE_MYSQL_URL", "")
TARGET_URL = os.environ.get("TARGET_PG_URL", "")

if not SOURCE_URL or not TARGET_URL:
    sys.exit("Set SOURCE_MYSQL_URL and TARGET_PG_URL (see module docstring).")

SOURCE_DB = SOURCE_URL.rsplit("/", 1)[-1]
SOURCE_SA_URL = SOURCE_URL.replace("mysql://", "mysql+aiomysql://", 1) + "?charset=utf8mb4"
TARGET_SA_URL = TARGET_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# The app engine must aim at the target before any app.* import (settings
# cache at import time); post() imports model metadata for the FK rebuild.
os.environ["DATABASE_URL"] = TARGET_SA_URL

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine  # noqa: E402

_TEMPLATE = Path(__file__).with_name("shuushuu.load.template")
_EXCLUDED = ("alembic_version",)  # source-only tw_* tables are excluded in the template


def _target_engine(**kwargs: object) -> AsyncEngine:
    return create_async_engine(TARGET_SA_URL, **kwargs)


async def preflight() -> None:
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", "alembic_pg")
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()

    engine = _target_engine()
    async with engine.connect() as conn:
        current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        if current != head:
            sys.exit(
                f"preflight: target at revision {current!r}, chain head is {head!r} — "
                "build the schema first (runbook step 3)."
            )
        others = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
        ).scalar()
        if others:
            sys.exit(
                f"preflight: {others} other connection(s) on the target — stop every "
                "app process first (runbook step 4; a reloading dev server that "
                "re-seeds tables mid-load cost us an afternoon)."
            )
    await engine.dispose()

    source = create_async_engine(SOURCE_SA_URL)
    async with source.connect() as conn:
        null_favs = (
            await conn.execute(text("SELECT COUNT(*) FROM favorites WHERE fav_date IS NULL"))
        ).scalar()
        if null_favs:
            sys.exit(
                f"preflight: {null_favs} favorites rows with NULL fav_date — the model "
                "declares NOT NULL and the chain-built schema enforces it, so the load "
                "will reject them. Apply the source data fix (runbook step 2) first."
            )
        timeouts = (
            await conn.execute(text("SELECT @@global.net_read_timeout, @@global.net_write_timeout"))
        ).one()
        if min(timeouts) < 600:
            print(
                f"preflight WARNING: source net_read/write_timeout = {tuple(timeouts)}; "
                "raise both to 600 (runbook step 5) or the big-table copies will drop."
            )
    await source.dispose()
    print("preflight: ok")


async def prep() -> None:
    engine = _target_engine()
    async with engine.begin() as conn:
        # Source carries a stray legacy column the models don't have.
        await conn.execute(text("ALTER TABLE tags ADD COLUMN IF NOT EXISTS tw_tagid integer"))
        tables = [
            row[0]
            for row in await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            if row[0] not in _EXCLUDED
        ]
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            await conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    print(f"prep: {len(tables)} tables truncated, tw_tagid staged")


def load() -> None:
    rendered = (
        _TEMPLATE.read_text()
        .replace("{{SOURCE}}", SOURCE_URL)
        .replace("{{TARGET}}", TARGET_URL)
        .replace("{{SOURCE_DB}}", SOURCE_DB)
    )
    workdir = Path(tempfile.mkdtemp(prefix="pg-migration-"))
    load_file = workdir / "shuushuu.load"
    load_file.write_text(rendered)
    log_file = workdir / "pgloader.log"
    print(f"load: pgloader starting (log: {log_file})")
    with log_file.open("w") as log:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-t",
                # --userns=host is required, not a convenience: a daemon with
                # userns-remap enabled refuses --network host outright, and
                # without it the remapped container root cannot read the 0600
                # load file below (it holds both URLs, credentials included, so
                # loosening the mode is not the trade to make).
                "--userns=host",
                "--network",
                "host",
                "-v",
                f"{load_file}:/shuushuu.load:ro",
                "dimitri/pgloader:latest",
                "pgloader",
                "--no-ssl-cert-verification",
                "/shuushuu.load",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    tail = log_file.read_text().splitlines()[-25:]
    print("\n".join(tail))
    output = log_file.read_text()
    if result.returncode != 0 or "KABOOM" in output or "FATAL" in output:
        sys.exit(f"load: pgloader failed (exit {result.returncode}); full log: {log_file}")
    print("load: complete")


async def post() -> None:
    from sqlalchemy.schema import AddConstraint
    from sqlmodel import SQLModel

    import app.main  # noqa: F401  (registers all tables on SQLModel.metadata)

    engine = _target_engine()
    async with engine.begin() as conn:
        # pgloader recreates FKs from source definitions with its own names;
        # drop everything and re-add the models' set so the end state is
        # deterministic regardless of how the load run went.
        await conn.execute(
            text(
                "DO $$ DECLARE r record; BEGIN "
                "FOR r IN SELECT conrelid::regclass AS tbl, conname "
                "FROM pg_constraint WHERE contype = 'f' "
                "LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname); "
                "END LOOP; END $$"
            )
        )
    added = 0
    async with engine.connect() as conn:
        for table in SQLModel.metadata.tables.values():
            for fk in table.foreign_key_constraints:
                await conn.execute(AddConstraint(fk))
                await conn.commit()
                added += 1
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE tags DROP COLUMN IF EXISTS tw_tagid"))
        await conn.execute(
            text(
                "DO $$ DECLARE r record; BEGIN FOR r IN "
                "SELECT c.table_name, c.column_name, "
                "pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) AS seq "
                "FROM information_schema.columns c "
                "WHERE c.table_schema = 'public' AND c.column_default LIKE 'nextval%' "
                "LOOP EXECUTE format("
                "'SELECT setval(%L, COALESCE((SELECT MAX(%I) + 1 FROM %I), 1), false)', "
                "r.seq, r.column_name, r.table_name); END LOOP; END $$"
            )
        )
        await conn.execute(text("ANALYZE"))
    await engine.dispose()
    print(f"post: {added} FK constraints re-added from models, sequences set, analyzed")


async def validate() -> None:
    from app.core.pg_triggers import disabled_triggers

    source = create_async_engine(SOURCE_SA_URL)
    target = _target_engine()

    async with source.connect() as conn:
        source_tables = {
            row[0]
            for row in await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :db AND table_type = 'BASE TABLE'"
                ),
                {"db": SOURCE_DB},
            )
        }
    async with target.connect() as conn:
        target_tables = {
            row[0]
            for row in await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }

    ignorable = {t for t in source_tables if t.startswith("tw_")} | set(_EXCLUDED)
    common = sorted((source_tables & target_tables) - ignorable)
    mismatched = []
    for table in common:
        async with source.connect() as conn:
            source_count = (await conn.execute(text(f"SELECT COUNT(*) FROM `{table}`"))).scalar()
        async with target.connect() as conn:
            target_count = (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar()
        status = "OK  " if source_count == target_count else "DIFF"
        if source_count != target_count:
            mismatched.append(table)
        print(f"{status} {table:<34} source={source_count} target={target_count}")

    for table in sorted(source_tables - target_tables - ignorable):
        print(f"NOTE {table:<34} exists only in source")
    for table in sorted(target_tables - source_tables - set(_EXCLUDED)):
        print(f"NOTE {table:<34} exists only in target (loads empty)")

    # Counts can't see this: pgloader's `disable triggers` takes the counter
    # triggers down with the FK internals, and only the FKs get rebuilt (post).
    # A load that died partway leaves the counters inert on a database whose
    # every row count matches.
    async with target.connect() as conn:
        inert = await disabled_triggers(conn)
    for name in inert:
        print(f"DOWN {name:<34} trigger disabled")

    await source.dispose()
    await target.dispose()
    if mismatched:
        sys.exit(f"validate: count mismatch in {len(mismatched)} table(s): {mismatched}")
    if inert:
        sys.exit(
            f"validate: {len(inert)} trigger(s) left disabled by the load — counters "
            "would silently stop updating. Re-enable them (ALTER TABLE ... ENABLE "
            "TRIGGER USER, owner is enough) and rerun validate before taking writes."
        )
    print(f"validate: {len(common)} tables match, all triggers enabled")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "prep", "load", "post", "validate", "all"])
    command = parser.parse_args().command
    steps: dict[str, Callable[[], object]] = {
        "preflight": lambda: asyncio.run(preflight()),
        "prep": lambda: asyncio.run(prep()),
        "load": load,
        "post": lambda: asyncio.run(post()),
        "validate": lambda: asyncio.run(validate()),
    }
    if command == "all":
        for name in ("preflight", "prep", "load", "post", "validate"):
            print(f"=== {name}")
            steps[name]()
    else:
        steps[command]()


if __name__ == "__main__":
    main()
