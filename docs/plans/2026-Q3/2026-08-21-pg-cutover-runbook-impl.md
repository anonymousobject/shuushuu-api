# Postgres cutover runbook — plan

**Date:** 2026-08-21
**Follows:** ADR-0008/0009/0010; closes the last item of the transition list
from docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md.

## What ships

1. `docs/postgres-cutover-runbook.md` — the operational procedure, distilled
   from the 2026-08-20/21 prod-scale rehearsals: pre-cutover source data
   fixes (the NULL `fav_date` drift), chain-built schema, write freeze,
   source timeout raises, the migration run, validation beyond counts (the
   ILIKE lesson), flip/rollback mechanics, and post-cutover watch items
   including the MariaDB retirement PR list. A living doc in `docs/`, not a
   point-in-time plan.
2. `scripts/pg_migration/` — the hardened tooling the runbook drives:
   - `shuushuu.load.template`: the pgloader command file with every
     rehearsal-earned setting annotated (data-only, concurrency 1, small
     batches, cast rules, schema rename, exclusions).
   - `migrate.py`: idempotent subcommands `preflight` (chain-head check,
     no-other-clients enforcement, source NULL-fav_date refusal, timeout
     warning), `prep` (temp `tw_tagid`, truncate), `load` (dockerized
     pgloader, log capture, hard fail on KABOOM/FATAL), `post` (FK
     normalization to model names, sequence setval, ANALYZE), `validate`
     (per-table source-vs-target counts, one-sided table NOTEs), `all`.

## Acceptance

A full rehearsal against a scratch target (`shuushuu_rehearsal`) built from
the alembic_pg chain: `migrate.py all` exits zero with every common table
count-matched, after the runbook's source fav_date fix is applied to the dev
source. mypy clean on the new script.
