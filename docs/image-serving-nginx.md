# Image Serving via Nginx

## Overview

Images are served directly by nginx for optimal performance, rather than going through the FastAPI application server. This approach:

- **Reduces FastAPI load** - Static files don't consume application resources
- **Improves performance** - nginx is highly optimized for static file serving
- **Enables caching** - Long cache headers (1 year) for immutable image files
- **Scales better** - Can add CDN or separate image servers later

## Architecture

```
Client Request
    ↓
nginx (port 80/3000)
    ├─ /api/v1/* → FastAPI (port 8000)
    └─ /storage/fullsize/* → Filesystem (/shuushuu/images/fullsize/)
    └─ /storage/thumbs/* → Filesystem (/shuushuu/images/thumbs/)
```

## Configuration

### Backend (FastAPI)

**File: `app/config.py`**
```python
IMAGE_BASE_URL: str = "http://localhost:3000"
```

This setting controls the base URL prepended to image paths in API responses.

**Environment Variable:**
```bash
IMAGE_BASE_URL=http://localhost:3000  # Development
IMAGE_BASE_URL=https://your-domain.com  # Production
IMAGE_BASE_URL=https://cdn.your-domain.com  # With CDN
```

**File: `app/schemas/image.py`**
```python
@computed_field
@property
def url(self) -> str:
    """Generate image URL"""
    return f"{settings.IMAGE_BASE_URL}/storage/fullsize/{self.filename}.{self.ext}"

@computed_field
@property
def thumbnail_url(self) -> str:
    """Generate thumbnail URL"""
    return f"{settings.IMAGE_BASE_URL}/storage/thumbs/{self.filename}.jpeg"
```

These computed fields generate complete URLs that the frontend can use directly.

### Nginx

**File: `docker/nginx/frontend.conf.template`**
```nginx
# Serve images directly from storage
location ^~ /storage/fullsize/ {
    alias ${STORAGE_PATH}/fullsize/;
    autoindex off;
    expires 1y;
    add_header Cache-Control "public, immutable";
}

location ^~ /storage/thumbs/ {
    alias ${STORAGE_PATH}/thumbs/;
    autoindex off;
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

The `^~` prefix gives these locations priority over regex locations.

### Frontend (SvelteKit)

The frontend simply uses the URLs returned by the API:

```svelte
<!-- Image detail page -->
<a href={data.image.url}>
  <img src={data.image.thumbnail_url} alt={data.image.title} />
</a>

<!-- Gallery page -->
<img src={img.thumbnail_url} alt={`Image ${img.image_id}`} />
```

No manual URL construction needed - the API provides complete URLs.

## API Response Example

```json
{
  "image_id": 1111520,
  "filename": "2024-11-15-1111520",
  "ext": "jpeg",
  "url": "http://localhost:3000/storage/fullsize/2024-11-15-1111520.jpeg",
  "thumbnail_url": "http://localhost:3000/storage/thumbs/2024-11-15-1111520.jpeg",
  ...
}
```

## Deployment Considerations

### Development
- Images served via nginx on port 3000
- `IMAGE_BASE_URL=http://localhost:3000`

### Production
- Configure nginx to serve images from filesystem or mounted volume
- Set `IMAGE_BASE_URL` to your domain (e.g., `https://e-shuushuu.net`)
- Consider adding a CDN for global distribution

### CDN Integration
To serve images from a CDN:

1. Sync images to CDN storage (S3, Cloudflare R2, etc.)
2. Set `IMAGE_BASE_URL=https://cdn.your-domain.com`
3. Images will automatically use CDN URLs in API responses

No frontend changes needed - it just uses whatever URLs the API provides.

## Cache Headers

Images use aggressive caching since they're immutable (filename includes upload date):

```nginx
expires 1y;
add_header Cache-Control "public, immutable";
```

This means:
- Browsers cache for 1 year
- `immutable` tells browsers the file will never change
- Reduces bandwidth and improves load times

## Migration Notes

Previous setup had FastAPI serving images via `app.mount()`:
```python
# Old code (removed)
app.mount("/storage/fullsize", StaticFiles(directory=f"{settings.STORAGE_PATH}/fullsize"))
app.mount("/storage/thumbs", StaticFiles(directory=f"{settings.STORAGE_PATH}/thumbs"))
```

This was replaced with nginx serving for better performance.
