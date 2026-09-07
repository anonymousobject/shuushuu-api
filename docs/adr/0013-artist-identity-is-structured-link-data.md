# Artist identity is structured link data, not an alias-tag convention

An artist's account on an external site is recorded as `(site, external_id)`
on the `tag_external_links` row that carries the profile URL, parsed from the
URL when the link is written. Identity lives on the canonical artist tag only,
and one identity has one owner. The legacy `Pixiv [ID]` alias tags keep
working but are no longer the mechanism. Settled in PRs #376 (API) and
frontend #412; pixiv is the only site so far.

The problem this settled: the way to make an artist findable by pixiv ID was
a mod-created alias tag titled `Pixiv 21412050`. That is a text convention
with no key behind it. Nothing checked that the ID was unused, so duplicate
artist tags for one account accumulated (99 adjacent-id pairs on the prod
copy); the fuzzy search matched neighbouring IDs (fixed separately, PR #301);
and coverage depended on someone having made the alias — a pixiv URL sitting
in the tag's links or description gave nothing.

## What the backfill found

Run on the prod copy (2026-09-05), assigning from links, then alias titles,
then description URLs, then `(Pixiv N)` title suffixes, then bare
`pixiv N` text:

| | |
|---|---|
| identities assigned | 68,259 |
| from existing links / alias titles / description URLs / titles / bare text | 1,441 / 45,532 / 19,411 / 1,433 / 442 |
| skipped as anomalies (needs a mod decision) | 2,162 |
| artist tags with no pixiv identity from any source | 35,956 |
| duplicate `(site, external_id)` after the run | 0 |

The backfill never guesses: a second claimant for an ID, a description naming
two IDs, an alias title that does not parse exactly, or an identity link
parked on an alias tag is reported, not written. The anomaly list is a mod
workbook, and the zero-duplicates result is what makes the unique index a
safe follow-up rather than a precondition.

## Considered Options

- **Keep the alias convention** — no schema change. Rejected: it is the
  source of the duplicates, it cannot express "this ID is taken", and 19,000+
  artists already had the URL on the tag with no alias.
- **Auto-create alias tags from links** — the mods' counter-proposal, because
  the alias row is where they look. Rejected as the mechanism: an alias title
  is still text (no uniqueness, no site), a second source of truth beside the
  URL, and it would double the alias table. It remains possible as a
  presentation layer on top of the structured data if parity ever needs it.
- **Identity on the artist tag itself** — a `pixiv_id` column on `tags`.
  Rejected: one column per site, and the URL that proves the identity already
  lives on the link row; a tag with two links for one account (legacy
  `member.php` and modern `/users/`) needs the identity on exactly one of
  them.
- **Structured identity on the link row (chosen)** — `site`/`external_id`
  as `ci_string` (ADR-0008), filled by a parser on write, exact lookup in
  search ranked above the fuzzy hits, the write path refusing a second
  owner (409 naming the first) and refusing identity on an alias tag (409
  naming the canonical), and a backfill that reports rather than resolves
  conflicts.

## Consequences

- Only the new path gets the new behaviour. An artist set up alias-only gets
  no identity row, no tag-page identity, no upload suggestion from a pasted
  URL, and no duplicate guard. For new artists mods add the profile link
  instead of an alias; existing aliases coexist and retiring them is gated on
  mod sign-off, as is the script that strips harvested URLs out of
  descriptions.
- `UNIQUE (site, external_id)` is a follow-up migration on both chains. The
  data already satisfies it; the index turns the application-level 409 into
  a guarantee under concurrent writes.
- Adding a site means a parser case and a display name, not a schema change.
  Case-insensitivity is already in place for handle-shaped IDs.
- Anything that moves links between tags must keep the one-owner rule. The
  alias-set path in `update_tag` does (it moves every link to the canonical);
  a future path that copies links would not.
