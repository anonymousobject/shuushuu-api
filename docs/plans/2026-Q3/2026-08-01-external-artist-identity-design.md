# External Artist Identity (pixiv IDs without alias tags)

**Status:** Draft for mod review. Not scheduled. The `disableOnNumbers`
search fix shipped independently as api#301.

## Problem

We identify pixiv-based artists by creating a "Pixiv [ID]" tag aliased to the
artist tag. Search resolves the alias, so typing an ID shows "Pixiv 21412050 →
artist". Mods like two things about this: pasting an ID finds the artist, and
the ID is visible in the suggestion title so the match can be verified without
opening the tag.

The mechanism has problems:

- The identity is a **title-string convention**, not data. Tooling has to
  string-parse alias titles (`is_pixiv_alias_title`), and nothing enforces
  uniqueness — `tags(title, type)` has no unique constraint, so two artists
  can claim the same ID.
- **Coverage is incomplete.** Older artist tags predate the convention: some
  have no alias at all, some have the pixiv URL or ID buried in `desc`.
- It creates thousands of pseudo-tags that pollute the tag namespace, exports,
  and counts.
- Upload URL-import already extracts the pixiv/twitter/bluesky artist ID
  (`ImportResult.artist_id`) but has nowhere to look it up, so it is discarded.
- Fuzzy matching on numbers made ID search untrustworthy (fixed separately;
  see below).

## What we verified empirically (dev, 2026-08-01)

Meilisearch indexes `title`, `desc`, and `external_urls` as searchable. URL
tokenization means a bare ID like `21412050` **already matches** an artist
whose pixiv URL is in their external links — no alias involved. `pixiv
21412050` also matches (the `pixiv` token comes from `pixiv.net`).

Typo tolerance was the real hazard. With default settings, `21412051`
(off-by-one) silently returned the artist for `21412050` — WhiteKitten's
"close numbers" complaint, confirmed. `typoTolerance.disableOnNumbers: true`
fixes exactly this:

| Query | Before | After |
|---|---|---|
| `21412050` (exact) | artist ✓ | artist ✓ |
| `21412051` (off-by-one) | **wrong artist, silently** | 0 hits ✓ |
| `2141205` (partial) | 6 hits incl. unrelated IDs | true-prefix hits only |
| `21412` (short partial) | 112 hits of numeric soup | 3 true-prefix hits |

Text typo tolerance is unaffected (`kinomto` still finds "Kinomoto").
This ships on its own as the `fix/search-numeric-typo-tolerance` PR, and it
improves the *current* alias system too — the noisy matches mods see today are
typo tolerance over alias titles.

## Design

Identity is derived from data mods already maintain: the tag's external links.

### 1. Structured identity on `tag_external_links`

Add nullable `site` and `external_id` columns, populated automatically by a
URL-parser registry whenever a link is added or edited. **v1 ships the pixiv
parser only (POC scope, decided 2026-08-01)**; twitter/x and bluesky are the
expected follow-ons if the POC lands well, each a small parser + tests PR. Adding
`https://www.pixiv.net/users/21412050` as an external link **is** the identity
entry — no new mod workflow, one step instead of today's two (create alias tag
+ set alias_of). The pixiv parser must accept every URL form in the wild:
`/users/{id}`, language-prefixed `/en/users/{id}`, legacy
`member.php?id={id}` / `member_illust.php?id={id}`, and `touch.pixiv.net`
variants — old descs and links use the legacy forms.

Because identity rides the link row, it inherits infrastructure links already
have: audit history (api#299), `dead_at` + `archive_url` (a deleted pixiv
account keeps its identity, flagged dead), and meilisearch indexing.

### 2. Duplicate-artist guard

Unique index on `(site, external_id)`. Adding a link whose parsed identity
already belongs to another tag returns a 409 naming that artist.

**Decided (2026-08-01): hard unique.** Genuinely shared accounts
(duos/circles posting under one pixiv account) are rare enough to handle
manually — merge the tags or drop the link — and the DB-level guarantee is
half the point of structured identity. Relax to warn-with-override only if
reality objects.

**Migration order matters:** the columns land nullable and unindexed first;
the unique index is added only after the backfill's conflict cases have been
hand-resolved. Adding the index up front would make the backfill abort on the
first pre-existing duplicate.

### 3. Search

- Fuzzy layer: unchanged meilisearch behavior (bare ID, partial ID,
  `pixiv <id>`, URL fragments), made safe by `disableOnNumbers`.
- Exact layer: when the query parses as an ID or profile URL, an exact
  `(site, external_id)` lookup returns a synthesized suggestion row rendered
  like today's alias rows ("pixiv 21412050 → artist"), ranked first. The
  suggestion payload can also carry each artist's identities so the canonical
  row itself shows the ID — preserving "verify without opening the tag",
  backed by an exact lookup instead of a string match that looks right.

### 3b. Tag-page presentation (added 2026-08-07, mod feedback)

Identity entries render **inside the tag page's alias section**, in alias
typography, formatted `Pixiv 21412267` — the slot and format mods have used
for 15 years — but with an honest affordance: the entry **links out to the
pixiv profile** (with an external-link indicator) and carries a tooltip
naming its provenance ("Pixiv artist ID — from this tag's pixiv link").
Identity entries never appear in the tag-edit alias editor; they are managed
by editing the pixiv link itself. During alias coexistence, a real "Pixiv N"
alias tag matching a displayed identity is suppressed from the alias list
(hidden, not deleted) — the same dedup rule search uses. The external links
section still lists the raw URL: it remains the management surface
(dead-marking, archive, delete); the alias-slot entry is identification only.
This supersedes the earlier standalone identity badge on the tag page.

### 4. Upload auto-suggest

URL-import's `artist_id` + site feeds the same exact lookup; the upload form
pre-suggests the artist tag. Unknown ID → no suggestion (and correctly no
false "artist exists" signal).

### 5. Backfill (the heart of the migration)

Harvest from three sources. Run order: **existing links first** (they are
ground truth mods entered by hand), then alias titles, then desc — each later
source consults the identities established by earlier ones and reports
conflicts instead of writing:

1. **Existing links** — parse all `tag_external_links` URLs in place →
   populate `site`/`external_id` for every site with a registered parser
   (pixiv only in v1; re-running after a new parser lands picks up that site).
2. **Alias titles** — parse "Pixiv [ID]" aliases → ensure a
   `pixiv.net/users/{id}` link on the canonical tag.
3. **`desc` text** — regex-harvest pixiv URLs/IDs from artist descriptions →
   create the link. `desc` itself is not modified in v1.

Dry-run first, producing an anomaly report for hand review: unparseable alias
titles, one ID claimed by two artists, desc IDs conflicting with existing
links, aliases pointing at non-artist tags. The script auto-applies only clean
cases. Finish with a full `scripts/reindex_search.py` run.

### 6. Rollout and alias retirement

Everything except retirement is **additive** and coexists with the alias
system — the alias and the link resolve to the same artist. Rollout:

1. Build v1 (columns, pixiv parser, dup guard, exact search layer, upload
   auto-suggest, backfill dry-run).
2. Backfill on dev; mods use it there and judge outcome parity.
3. Only after mod sign-off: delete the migrated "Pixiv [ID]" alias tags
   (dev → test → prod, with the backfill re-run per environment).

## Outcome parity checklist (the mods' bar)

| Today | New system |
|---|---|
| Paste ID → find artist | Preserved (meili URL tokens + exact layer) |
| See ID in the suggestion, no tag-opening needed | Preserved (synthesized exact row + identities in payload) |
| Wrong-but-close ID matches | **Removed** (`disableOnNumbers`, ships first) |
| Partial-ID typing autocompletes | Preserved (true prefixes only) |
| Mod creates identity | Easier: add the URL link (often already done) |
| Identity visible on tag page | Improved: rendered in the alias section (see "Tag-page presentation") |

## Implementation notes / open items

- During dev testing, `GET /search?q=21412050` reported `total: 2` with 1
  hit — something counts a second document (likely the alias tag) but filters
  it from display. Understand before relying on totals; disappears with
  retirement.
- Coverage report: the existing (uncommitted) export script becomes "artists
  with no pixiv identity" once identity is structured — no string parsing.
- Frontend: suggestion rendering for the synthesized exact row; upload-form
  auto-suggest chip.
- Out of scope v1: non-URL identifiers, editing `desc` text, per-site display
  niceties beyond pixiv.
