# Case-insensitive natural keys use citext on Postgres

`users.username`, `users.email`, and `tags.title` are typed through
`ci_string(n)` (`app/models/types.py`): `VARCHAR(n)` on MariaDB, `CITEXT` on
Postgres, with the length caps enforced on Postgres by `ck_*_len` CHECK
constraints since citext has no length modifier. Everything else stays
case-sensitive on Postgres.

The problem this settled: on MariaDB, `utf8mb4_unicode_ci` makes *every*
string comparison case-insensitive, and three natural keys silently depend on
that — login (`WHERE username = :u`), registration/rename uniqueness, and
tag-title dedupe. Postgres compares case-sensitively by default, and the
Postgres POC (`docs/plans/2026-Q3/2026-08-20-postgres-poc-impl.md`) reproduced
the breakage immediately: a login that works on prod with `anonymous_objecT`
returned 401 on the Postgres-backed dev stack. Content columns (captions,
comments, descriptions) carry no equality semantics and keep the Postgres
default; search paths handle their own case folding (ILIKE fallbacks,
Meilisearch).

## Considered Options

- **Keep Postgres's case-sensitive equality** manufactures "can't log in"
  failures for any user whose typed casing differs from their registration,
  and lets `Admin` register next to `admin`. No mainstream app treats auth
  identifiers this way; rejected outright.
- **`LOWER()` on both sides plus functional indexes** needs no extension, but
  its correctness is per-call-site discipline: every present and future lookup
  must remember the `LOWER()`, and one miss is a subtle production bug (the
  feasibility doc's "sleeper risk"). The users-list search already does this
  correctly — for a *filter*; for the identity keys the guarantee belongs in
  the schema, not in each query.
- **ICU nondeterministic collations** are the most Unicode-correct answer but
  the least-trodden path: pattern matching on such columns is restricted on
  PG 17, ORM/tooling support is thin, and no one proposing changes here will
  have muscle memory for it.
- **citext** (chosen) restores the exact legacy semantics at the type level —
  every existing and future query is case-insensitive on these columns with
  no call-site changes — and the MariaDB data is guaranteed free of
  case-variant duplicates because `_ci` uniqueness has been enforcing that
  for years. It is bundled contrib (trusted extension since PG 13), not a
  third-party dependency.

## Consequences

- `CREATE EXTENSION citext` must precede schema creation on any new Postgres
  database; it lives in `public`, so a `DROP SCHEMA public CASCADE` reset
  removes it (`scripts/postgres_poc.py setup` handles the ordering, and a
  future Postgres baseline migration must do the same).
- The length caps on these three columns are CHECK constraints on Postgres,
  not typmods; Pydantic `max_length` remains only the friendly 422 at the API
  boundary. SQLModel table models skip validation on instantiation, so the
  database CHECK is the sole universal enforcement — MariaDB's `VARCHAR(n)`
  played that role before.
- citext folds via `lower()`, not full Unicode case folding — same class of
  folding the legacy collation did for this data; acceptable for these keys.
- The CHECKs are deliberately not in the models' `__table_args__`: there they
  would change the MariaDB DDL and break the schema-sync tests. They belong to
  the Postgres side only (today the POC setup script; later the baseline
  migration).
