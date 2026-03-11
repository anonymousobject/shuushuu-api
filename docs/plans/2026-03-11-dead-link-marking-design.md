# Dead Link Marking for Tag External Links

## Problem

Artist and source websites go down over time. Mods need a way to mark external links as dead while preserving the original URL, and optionally provide an archived version of the page (e.g., Wayback Machine).

Reference: the "Site" section on legacy wiki pages like `Kamui_Marimo` shows this pattern — original URL displayed with "(Archived here)" linking to a Wayback Machine snapshot.

## Design

### Model Changes

Add two nullable columns to `tag_external_links`:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `dead_at` | `datetime \| None` | `None` | When the link was marked dead. Null means alive. |
| `archive_url` | `varchar(2000) \| None` | `None` | URL to an archived version of the page. |

These fields are independent — a link can have an archive URL without being marked dead, or be marked dead without an archive URL.

### Schema Changes

**`TagExternalLinkResponse`** — add two fields:
- `dead_at: UTCDatetime | None`
- `archive_url: str | None`

**New `TagExternalLinkUpdate`** schema:
- `is_dead: bool | None = None` — server sets `dead_at = now()` on `True`, clears on `False`
- `archive_url: str | None = None` — sets or clears the archive URL

Using `is_dead` boolean rather than accepting a raw timestamp keeps the API simple and consistent (server controls timestamps).

### API Changes

**`PATCH /tags/{tag_id}/links/{link_id}`**
- Permission: `TAG_UPDATE` (same as add/delete link)
- Request body: `TagExternalLinkUpdate`
- Response: `TagExternalLinkResponse`
- 404 if link not found or doesn't belong to tag

Behavior:
- `is_dead: true` → sets `dead_at` to current time (no-op if already dead)
- `is_dead: false` → clears `dead_at` (no-op if already alive)
- `archive_url: "<url>"` → sets archive URL (validated: http/https, max 2000 chars)
- `archive_url: null` → clears archive URL (when explicitly provided as null)
- Omitted fields are unchanged

### Migration

Standard Alembic migration adding two nullable columns. No data migration needed.

## Not Included

- Automatic dead link detection or crawling
- Changes to the tag `desc` field
- Frontend/UI changes
