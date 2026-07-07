# phpBB3 Forum Import — Design

**Date:** 2026-07-07
**Status:** Approved design, pre-implementation
**Repo:** shuushuu-api only (frontend needs no changes)
**Depends on:** the forum feature (`feat/forum`, PR shuushuu-api#263). This work builds on the `forum_categories`/`forum_threads`/`forum_posts` tables and must land after the forum merges — this branch is stacked on `feat/forum`; rebase onto `main` once that merges.

## Goal

Import the retired phpBB3 forum's content into the new forum system as locked, read-only threads, integrated with live browsing/search and correctly attributed to current user accounts. This is the "read-only archive" decided during the forum brainstorm, realized through the new forum's own `locked` flag rather than a separate archive surface.

## Source data (investigated)

Three restored databases on the dev MariaDB (`localhost:3306`):

- **`shuushuuphpbb3`** — phpBB 3.3.14 dump. Content: 9 real forums (plus 3 empty container categories), 61 topics, 14,704 posts (all `post_visibility=1`, no soft-deleted backlog), 500 distinct posters, 357 image attachments. Post text is stored as **s9e-TextFormatter XML** (the `<r>`/`<t>` format), charset `utf8mb3`.
- **`php_shuu`** — final dump of the legacy PHP main site. Its `users` table has a `forum_id` column giving the authoritative phpBB→site user-id mapping (1,353 linked accounts).
- Attachment files: all 357 present at `/sakura/backups/forums-2026-02-20/files/`.

The source charset is `utf8mb3` (BMP-only); the new forum tables are `utf8mb4`. Importing `utf8mb3` into `utf8mb4` is a lossless widening — no conversion risk.

Forum structure and target tiers:

| phpBB forum | topics | posts | → category access |
|---|---|---|---|
| Mod Forum | 4 | 4,406 | `FORUM_ACCESS_STAFF` |
| Tagging team | 6 | 1,645 | `FORUM_ACCESS_TAGGER` |
| General | 17 | 6,037 | public |
| Image Requests | 4 | 1,971 | public |
| Suggestions | 12 | 407 | public |
| Image/Art Discussion | 5 | 40 | public |
| RP Forum | 10 | 183 | public |
| Gaming | 3 | 15 | public |
| Bug Reports | 0 | 0 | public (starts empty) |

## Decisions (settled during brainstorm)

- **Attachments:** text links (no forum renderer change), files rehosted to R2 under a dedicated prefix separate from board images.
- **Categories:** shared — the 9 imported categories are the live forum's categories; new threads are allowed in them, only the imported threads are locked. (Avoids a permanent parallel set of archive-only forums.)
- **Unmapped authors:** a single system "Archived User" account, with the original poster name stored and displayed; remappable later.
- **Attribution trust:** `forum_id` map (authoritative, rename-safe) + email matches → real accounts; username-only matches → Archived User (a reused username could mis-attribute). Everything remains remappable.

## Architecture

A staged, idempotent, re-runnable management script. No API endpoint. Stages:

1. **Rehost attachments** to R2.
2. **Build the user map** (phpBB poster → site user).
3. **Convert and import** each forum→category, topic→locked thread, post→post.
4. **Verify** (counts, coverage, idempotency).

Units, each independently testable:

| Unit | Responsibility | Depends on |
|---|---|---|
| Alembic migration | provenance columns + unique indexes, seed "Archived User" | forum tables |
| `app/services/forum_import/s9e_convert.py` | s9e XML → new markdown (pure function) | nothing (stdlib XML) |
| `app/services/forum_import/user_map.py` | build phpBB poster → (site_user_id \| Archived) map | `php_shuu`, `shuushuuphpbb3` |
| `app/services/forum_import/attachments.py` | upload files to R2, return per-attachment URL | existing R2 client, backup dir |
| `scripts/import_forum_archive.py` | orchestrate stages; modes `--dry-run` / `--remap` | all of the above + forum models |
| forum post serialization tweak | display `legacy_username` for Archived-User posts | forum schema |

The converter is a pure function (XML string → markdown string) so it is unit-testable in isolation from any database.

### Source access

The script reads from **live MariaDB databases over a connection**, never by parsing `.sql` files (a mysqldump is a stream of SQL statements; parsing it in Python would reinvent a SQL parser and is out of scope). The two source databases are supplied as connection parameters — `--phpbb-url` (default the dev `shuushuuphpbb3`) and `--site-url` (default the dev `php_shuu`) — so the same script serves any environment.

Loading each `.sql` backup into a scratch database is therefore a one-command prerequisite (`mysql <scratch_db> < dump.sql`), run per environment before the import. The script only needs read access, and only to a handful of tables (`phpbb_forums`, `phpbb_topics`, `phpbb_posts`, `phpbb_users`, `phpbb_attachments`, and `php_shuu.users`).

## Schema changes

New Alembic migration (on the forum-import branch, after the forum tables exist):

- `forum_categories`: add `legacy_forum_id INT UNSIGNED NULL`, unique index.
- `forum_threads`: add `legacy_topic_id INT UNSIGNED NULL`, unique index.
- `forum_posts`: add `legacy_post_id INT UNSIGNED NULL` (unique index), `legacy_poster_id INT UNSIGNED NULL` (plain index), `legacy_username VARCHAR(255) NULL`.
- Seed one system user "Archived User": `active=0`, unusable password, a stable sentinel username; capture its `user_id` by lookup (not a hardcoded constant).

The unique `legacy_*` keys are the idempotency mechanism: every insert is guarded by "does a row with this legacy id already exist?" so a re-run inserts only what is missing. Native (non-imported) rows keep all `legacy_*` columns NULL.

## User attribution

Build `phpbb_poster_id → resolution`:

1. **`forum_id` (authoritative):** `php_shuu.users.forum_id = phpbb_user_id` → that row's `user_id`. Rename-safe; resolved 397 posters in analysis, and correctly handled 5 renamed accounts username-matching would have missed or mis-assigned.
2. **email (strong):** phpBB `user_email` == a current user's email → that user. ~11 more.
3. **otherwise → Archived User.** The ~92 username-only and truly-unmapped posters (username-only is unsafe: a freed username may now belong to someone else).

Every imported thread and post stores `legacy_poster_id` and `legacy_username` **regardless of resolution**, so:
- attribution is auditable, and
- correcting any post later is a one-line `UPDATE forum_posts SET user_id=<new> WHERE legacy_poster_id=<phpbb_id>` (plus the thread author / `last_post_user_id`). The `--remap` mode re-runs resolution and applies exactly these updates when new `forum_id` links appear.

**Assumption:** `php_shuu.user_id` equals the current site's `user_id` (the FastAPI site inherited the legacy user base). Near-certain; confirm with one query against prod before the prod run.

## Post display (only API-behavior change)

In the forum post serialization (`_post_response` / the `UserSummary` build), when a post's `user_id` is the Archived User account **and** `legacy_username` is set, build the `UserSummary` with `username = legacy_username`. The name links to the single Archived-User profile. Real-account posts are unchanged — they show the current username (rename-aware) and link to the real profile. No frontend change: the frontend renders whatever `UserSummary.username` it receives.

## s9e XML → markdown converter

Deterministic tree-walk over post `post_text` (`<t>plain</t>` or `<r>rich</r>`). Element mapping:

| s9e element | → new markdown |
|---|---|
| `<t>text</t>` | text (XML-unescaped) |
| `<B>…</B>` / `<I>…</I>` | `**…**` / `*…*` |
| `<U>…</U>` | inner text (renderer has no underline) |
| `<QUOTE author="X">…</QUOTE>` | `[quote="X"]…[/quote]` (nested supported) |
| `<QUOTE>…</QUOTE>` (anonymous) | `[quote]…[/quote]` |
| `<URL url="U">t</URL>` | `[t](U)` |
| `<IMG src="U">` | `[image](U)` (external URL, usually dead — kept as a link) |
| `<ATTACHMENT …>` / inline `[attachment=N]` | resolved to the rehosted R2 link (see Attachments) |
| `<E>emoji</E>` | its literal inner text |
| `<COLOR>/<SIZE>/<FONT>` | tag stripped, inner text kept (lossy — accepted; 386 / 176 / few posts) |
| `<LIST>/<LI>` | `- item` lines (13 posts) |
| `<CODE>` | inner text (renderer has no code blocks; 9 posts) |
| `<s>…</s>` / `<e>…</e>` (source markers) | dropped |
| `<br/>` | newline |

**Defensive rule:** any unrecognized element → recurse into it and keep its inner text. Nothing is silently dropped even if a tag name differs from the table above. Output is trimmed and passed through unchanged at import time; rendering happens later via the existing `parse_markdown` at read time (so `[quote]`, links, bold/italic, spoilers all work with no renderer change).

## Attachments

- Upload the 357 files from `/sakura/backups/forums-2026-02-20/files/` to R2 under a **dedicated prefix distinct from board images** (e.g. `forum-archive/`), reusing the app's existing R2 client. Key each object by `physical_filename` (already content-addressed), so re-runs skip already-uploaded files.
- `phpbb_attachments` joins to posts via `post_msg_id = post_id`. For each imported post, append its attachments (ordered by `attach_id`) as text-link lines at the end of the converted body: `📎 <real_filename>` → the R2 URL. Inline `[attachment=N]` placeholders in the body are removed (the appended list is the canonical presentation). `is_orphan=1` / PM attachments (`in_message=1`) are skipped.

## Import mechanics

- Category: one `forum_categories` row per phpBB type-1 forum, with `legacy_forum_id`, title = `forum_name`, description = `forum_desc`, `sort_order` from phpBB `left_id`, and the tier from the table above (Mod Forum staff, Tagging team tagger, rest public/NULL).
- Thread: one `forum_threads` row per topic — title = `topic_title`, author = resolved poster of the topic's first post, `date` from `topic_time`, `pinned=false`, `locked=true`, `legacy_topic_id`.
- Posts: `forum_posts` rows inserted in chronological order (by `post_time`), so the earliest post gets the smallest `post_id` and becomes the thread's opening post; `date` from `post_time` → `DATETIME(6)`; body = converted markdown + attachment links; author resolved per user attribution; `legacy_post_id`, `legacy_poster_id`, `legacy_username` set.
- After a topic's posts are inserted, set the thread's denormalized `post_count`, `last_post_at`, `last_post_user_id` directly (single-threaded batch — no concurrency, so no locking-read concern).
- One transaction per topic. Idempotent via the `legacy_*` unique keys.
- **Modes:** default (insert missing), `--remap` (re-resolve and update `user_id`s only), `--dry-run` (report counts and coverage, write nothing).

## Testing

- **Converter unit tests** (`tests/services/test_s9e_convert.py`): real XML samples pulled from `shuushuuphpbb3` covering plain text, bold/italic, single and nested `<QUOTE>`, `<URL>`, `<IMG>`, `<ATTACHMENT>`, `<COLOR>`/`<SIZE>`, `<E>` emoji, and `<LIST>` → asserted markdown, plus an unknown-element case proving the defensive text-preserving fallback.
- **Import integration test** (`tests/integration/test_forum_import.py`), real DBs on dev, no mocks: after a run, assert 9 categories exist with the correct access tiers, 61 threads all `locked=true`, 14,704 posts, attribution coverage (forum_id + email → real users; the remainder → Archived User with `legacy_username` populated and surfaced by the serialization), attachment links resolve to the R2 prefix, a re-run creates no duplicates (idempotency), and `--remap` updates a seeded `user_id`.
- **User-map unit tests**: forum_id precedence over email over username; rename case resolves via forum_id.

## Rollout

1. **Dev:** apply the migration; run against the restored `shuushuuphpbb3` + `php_shuu` with a dev R2 prefix. Verify in the forum UI — browse imported threads, confirm Mod Forum is hidden from a non-staff account, spot-check rendering, attachment links, and author names (real vs Archived-User).
2. **Prod:** restore the two source DBs on/reachable from the prod DB host; confirm the `php_shuu.user_id == site user_id` assumption with one query; upload attachments to the prod R2 prefix; run the import; verify. The script is the deliverable, run once per environment.

## Scope boundaries (YAGNI)

**Not** imported: private messages (3,027), polls, user profiles/avatars, signatures. **Not** built: inline image rendering, a native forum upload feature. Old external hotlinks (Photobucket etc.) remain as dead links — unrecoverable. **Known minor:** imported threads have no read-tracking rows, so logged-in users see the 61 threads as unread until viewed; accepted (no special "mark archive read").
