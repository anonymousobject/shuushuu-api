# Image Serving via Nginx

## Overview

All image requests (`/images/*`, `/thumbs/*`, `/medium/*`, `/large/*`) are
proxied through FastAPI for permission checks before serving. FastAPI decides
the serving method:

- **X-Accel-Redirect** to an internal nginx location (local filesystem)
- **302 redirect** to a CDN URL or presigned R2 URL (when `R2_ENABLED=true`)

This approach ensures permission checks are always enforced while keeping
nginx responsible for actual file serving in the local-FS path.

## Architecture

```
Client Request
    ↓
nginx (port 80/443)
    ├─ /api/v1/*                → FastAPI (port 8000)
    ├─ /images/{filename}       → FastAPI (permission check)
    ├─ /thumbs/{filename}       → FastAPI (permission check)
    ├─ /medium/{filename}       → FastAPI (permission check)
    ├─ /large/{filename}        → FastAPI (permission check)
    └─ /internal/{type}/*       → Filesystem (internal only, via X-Accel-Redirect)
```

## Configuration

### Backend (FastAPI)

**File: `app/config.py`**
```python
IMAGE_BASE_URL: str = "http://localhost:3000"
```

This setting controls the base URL prepended to image paths in API responses
when R2 is disabled or the image hasn't been synced to R2 yet.

**File: `app/schemas/image.py`**

URL generation uses computed fields that route based on R2 state:

```python
def _should_use_cdn(self) -> bool:
    """Direct-CDN URL when R2 enabled, status is public, and object is in public bucket."""
    return (
        settings.R2_ENABLED
        and self.status in PUBLIC_IMAGE_STATUSES_FOR_R2
        and self.r2_location == R2Location.PUBLIC
    )

@computed_field
@property
def url(self) -> str:
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/fullsize/{self.filename}.{self.ext}"
    return f"{settings.IMAGE_BASE_URL}/images/{self.filename}.{self.ext}"

@computed_field
@property
def thumbnail_url(self) -> str:
    if self._should_use_cdn():
        return f"{settings.R2_PUBLIC_CDN_URL}/thumbs/{self.filename}.webp"
    return f"{settings.IMAGE_BASE_URL}/thumbs/{self.filename}.webp"
```

Medium/large variants only use CDN URLs when the variant status is `READY`
(not `PENDING`), so the media endpoint's fullsize fallback works during
variant generation.

### Frontend (SvelteKit)

The frontend uses URLs returned by the API directly:

```svelte
<a href={data.image.url}>
  <img src={data.image.thumbnail_url} alt={`Image ${data.image.image_id}`} />
</a>
```

No manual URL construction needed.

### API Response Example

```json
{
  "image_id": 1111520,
  "filename": "2024-11-15-1111520",
  "ext": "jpeg",
  "url": "http://localhost:3000/images/2024-11-15-1111520.jpeg",
  "thumbnail_url": "http://localhost:3000/thumbs/2024-11-15-1111520.webp",
  "medium_url": "http://localhost:3000/medium/2024-11-15-1111520.jpeg",
  "large_url": null,
  ...
}
```

When R2 is enabled and the image is in the public bucket:

```json
{
  "url": "https://cdn.e-shuushuu.net/fullsize/2024-11-15-1111520.jpeg",
  "thumbnail_url": "https://cdn.e-shuushuu.net/thumbs/2024-11-15-1111520.webp",
  ...
}
```

## URL Patterns

- `/images/{date}-{image_id}.{ext}` (e.g., `/images/2026-01-02-1112196.png`) - fullsize
- `/thumbs/{date}-{image_id}.webp` - thumbnail (always WebP)
- `/medium/{date}-{image_id}.{ext}` - medium variant (1280px edge, if available)
- `/large/{date}-{image_id}.{ext}` - large variant (2048px edge, if available)

## Nginx Configuration

### Protected image routes (proxy to FastAPI)

```nginx
location ~ "^/images/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9]+\.(png|jpg|jpeg|gif|webp)$" {
    proxy_pass http://api:8000;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# Same pattern for /thumbs/, /medium/, /large/
```

### Internal locations (X-Accel-Redirect targets)

```nginx
location /internal/fullsize/ {
    internal;
    alias ${STORAGE_PATH}/fullsize/;
    expires 1y;
    add_header Cache-Control "public, immutable";
}

location /internal/thumbs/ {
    internal;
    alias ${STORAGE_PATH}/thumbs/;
    expires 1y;
    add_header Cache-Control "public, immutable";
}

# Same pattern for /internal/medium/, /internal/large/
```

## Request Flow

### Local filesystem (R2 disabled or `r2_location=NONE`)

```
Client → nginx → FastAPI (permission check) → X-Accel-Redirect → nginx → file
```

1. nginx proxies the request to FastAPI
2. FastAPI checks visibility (status, ownership, permissions)
3. If authorized: returns `X-Accel-Redirect: /internal/{type}/{filename}.{ext}`
4. If unauthorized: returns 404 (not 403, to hide existence)
5. nginx serves from internal location

### R2 serving (`R2_ENABLED=true`)

```
Client → nginx → FastAPI (permission check) → 302 redirect → R2/CDN
```

- **Public images** (`r2_location=PUBLIC` and status is public): 302 to CDN
  domain. Both conditions must hold — a public-bucket image whose status just
  changed to protected falls back to the app endpoint until the sync job
  moves it.
- **Protected images** (`r2_location=PRIVATE`): 302 to a short-lived presigned
  URL against the private R2 bucket. Each request issues a fresh presigned URL.
- **Unsynced images** (`r2_location=NONE`): falls back to X-Accel-Redirect
  (same as local FS path above).

All 302 responses include `Cache-Control: no-store` so nginx and browsers
do not cache the redirect.

## Visibility Rules

| Status | Value | Public? |
|--------|-------|---------|
| ACTIVE | 1 | Yes |
| SPOILER | 2 | Yes |
| REPOST | -1 | Yes |
| REVIEW | -4 | No* |
| INAPPROPRIATE | -2 | No* |
| OTHER | 0 | No* |

*Protected images are visible to:
- The image uploader (owner)
- Users with `IMAGE_EDIT` or `REVIEW_VIEW` permission

### Authentication

Permission checks use the `access_token` HTTPOnly cookie. Anonymous users
can only view public-status images.

## Caching Considerations

Since the same URL can return different responses based on authentication:
- nginx must NOT cache responses from `/images/*`, `/thumbs/*`, `/medium/*`,
  `/large/*` (add `proxy_cache off;` if proxy caching is enabled)
- The `/internal/*` locations use immutable caching since they're only
  reached after a successful permission check
- R2 302 responses include `Cache-Control: no-store`

## Cache Headers

Images use aggressive caching on the internal/CDN paths since filenames are
immutable (they include the upload date):

```nginx
expires 1y;
add_header Cache-Control "public, immutable";
```
