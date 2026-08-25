# News Items API Design

## Summary

Add CRUD API endpoints for news items. Reads are public; writes require granular permissions (NEWS_CREATE, NEWS_EDIT, NEWS_DELETE). The News model and database table already exist.

## Endpoints

| Method | Path | Auth | Permission | Description |
|--------|------|------|------------|-------------|
| GET | `/api/v1/news` | None | None | List news, paginated, newest first |
| GET | `/api/v1/news/{news_id}` | None | None | Get single news item |
| POST | `/api/v1/news` | Required | NEWS_CREATE | Create news item |
| PUT | `/api/v1/news/{news_id}` | Required | NEWS_EDIT | Update news item (sets `edited` timestamp) |
| DELETE | `/api/v1/news/{news_id}` | Required | NEWS_DELETE | Delete news item |

## Schemas

- **NewsResponse**: `news_id`, `user_id`, `username` (from join), `title`, `news_text`, `date`, `edited`
- **NewsCreate**: `title` (required, max 128 chars), `news_text` (required)
- **NewsUpdate**: `title` (optional), `news_text` (optional) -- at least one must be provided
- **NewsListResponse**: `total`, `page`, `per_page`, `news: list[NewsResponse]`

Response shape is flat with `username` included from a users table join (matches comment endpoint pattern).

## Permissions

Add to the `Permission` enum in `app/core/permissions.py`:
- `NEWS_CREATE`
- `NEWS_EDIT`
- `NEWS_DELETE`

Database migration required to insert these into the `perms` table.

## Data Flow

- **List**: `SELECT news.*, users.username FROM news JOIN users USING(user_id) ORDER BY news_id DESC` with pagination
- **Create**: Insert row with `user_id` from authenticated user; `date` defaults server-side via `current_timestamp()`
- **Update**: Set `edited = NOW()` server-side when modified
- **Delete**: Hard delete (matches legacy PHP behavior)

## Content Handling

News text is stored as plain text. No server-side HTML processing or sanitization. The frontend handles rendering.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `app/schemas/news.py` | Create | NewsCreate, NewsUpdate, NewsResponse, NewsListResponse |
| `app/api/v1/news.py` | Create | Route handlers |
| `app/core/permissions.py` | Modify | Add NEWS_CREATE, NEWS_EDIT, NEWS_DELETE |
| `app/api/v1/__init__.py` | Modify | Register news router |
| `alembic/versions/xxx_add_news_permissions.py` | Create | Insert permission rows |
| `tests/` | Create | Unit and API tests |
