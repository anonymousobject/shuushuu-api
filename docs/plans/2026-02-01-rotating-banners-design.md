# Rotating Banner System Design

## Overview

Add a rotating, randomized banner system to the image board. Banners rotate every 10 minutes via Redis caching. Banners are classified by theme compatibility (dark/light) and size (small/medium/large).

## Requirements

- Banners rotate randomly every 10-15 minutes (cached)
- Theme compatibility: banners support dark theme, light theme, or both
- Size variants: small, medium, large (intrinsic to banner)
- Three-part banner support: left + middle + right images with spacing
- Full banner support: single image
- Future extensibility for user preferences (favorite/exclude banners, preferred size)

## Data Model

### Banners Table (new schema, replaces legacy)

```python
class BannerSize(str, Enum):
    small = "small"
    medium = "medium"
    large = "large"

class BannerBase(SQLModel):
    name: str                              # Internal identifier
    author: str | None = None              # Attribution (optional)
    size: BannerSize = BannerSize.medium

    # Image paths (relative to banner directory)
    full_image: str | None = None          # Single image for full banners
    left_image: str | None = None          # Three-part: left
    middle_image: str | None = None        # Three-part: middle
    right_image: str | None = None         # Three-part: right

    # Theme compatibility
    supports_dark: bool = True
    supports_light: bool = True

    # State
    active: bool = True

class Banners(BannerBase, table=True):
    __tablename__ = "banners"

    banner_id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        sa_column_kwargs={"server_default": text("current_timestamp()")}
    )
```

### Database Schema

```sql
CREATE TABLE banners (
    banner_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    author VARCHAR(255),
    size ENUM('small', 'medium', 'large') NOT NULL DEFAULT 'medium',
    full_image VARCHAR(255),
    left_image VARCHAR(255),
    middle_image VARCHAR(255),
    right_image VARCHAR(255),
    supports_dark BOOLEAN NOT NULL DEFAULT TRUE,
    supports_light BOOLEAN NOT NULL DEFAULT TRUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_active_dark_size (active, supports_dark, size),
    INDEX idx_active_light_size (active, supports_light, size)
);
```

### Future: User Banner Preferences

When implementing user customization:

```python
class UserBannerPreferences(SQLModel, table=True):
    user_id: int                           # FK to users
    banner_id: int | None                  # FK to banners (for favorite/exclude)
    preference: str | None                 # "favorite" | "exclude"
    preferred_size: BannerSize | None      # User's size preference
```

## API Endpoints

### GET /api/v1/banners/current

Returns the currently cached banner for the requested theme and size.

**Query Parameters:**
- `theme` (required): `dark` | `light`
- `size` (required): `small` | `medium` | `large`

**Response Schema:**

```python
class BannerResponse(SQLModel):
    banner_id: int
    name: str
    author: str | None
    size: BannerSize
    is_full: bool                          # True if full_image, False if three-part
    supports_dark: bool
    supports_light: bool

    # Computed URLs (base_url + relative path)
    full_image_url: str | None
    left_image_url: str | None
    middle_image_url: str | None
    right_image_url: str | None
```

**Behavior:**
1. Check Redis for `banner:current:{theme}:{size}`
2. Cache miss: query active banners matching theme and size, pick randomly, cache for 10 min
3. Cache hit: deserialize and return
4. No matching banners: return 404

### GET /api/v1/banners

Lists all active banners. Public endpoint.

**Query Parameters:**
- `theme` (optional): filter by theme compatibility
- `size` (optional): filter by size
- Standard pagination params

**Response:** List of `BannerResponse`

## Caching Mechanism

### Redis Keys

```
banner:current:dark:small
banner:current:dark:medium
banner:current:dark:large
banner:current:light:small
banner:current:light:medium
banner:current:light:large
```

### TTL

10 minutes (configurable via `settings.BANNER_CACHE_TTL`)

### Selection Logic

```python
async def get_current_banner(
    theme: str,
    size: str,
    db: AsyncSession,
    redis: Redis
) -> BannerResponse:
    cache_key = f"banner:current:{theme}:{size}"

    # Try cache
    cached = await redis.get(cache_key)
    if cached:
        return BannerResponse.model_validate_json(cached)

    # Cache miss: query eligible banners
    theme_filter = Banners.supports_dark if theme == "dark" else Banners.supports_light
    query = select(Banners).where(
        Banners.active == True,
        theme_filter == True,
        Banners.size == size
    )
    result = await db.execute(query)
    banners = result.scalars().all()

    if not banners:
        raise HTTPException(404, "No banners available for this theme and size")

    # Random selection
    selected = random.choice(banners)

    # Cache and return
    response = BannerResponse.from_banner(selected)
    await redis.setex(cache_key, settings.BANNER_CACHE_TTL, response.model_dump_json())
    return response
```

### Cache Invalidation

Not implemented in v1. Cache TTL is short enough (10 min) that manual invalidation is unnecessary. Add invalidation when admin CRUD endpoints are implemented.

## File Storage & Serving

### Storage Location

```
/shuushuu/banners/
├── summer_2024_full.png
├── winter_theme_left.png
├── winter_theme_middle.png
├── winter_theme_right.png
└── ...
```

### Nginx Configuration

```nginx
location /static/banners/ {
    alias /shuushuu/banners/;
    expires 7d;
    add_header Cache-Control "public, immutable";
}
```

### URL Computation

Database stores relative paths. API response computes full URLs:

```python
@computed_field
@property
def full_image_url(self) -> str | None:
    if self.full_image:
        return f"{settings.BANNER_BASE_URL}/{self.full_image}"
    return None
```

### Configuration

```python
# In app/config.py
BANNER_BASE_URL: str = Field(default="/static/banners")
BANNER_CACHE_TTL: int = Field(default=600)  # 10 minutes
```

## Migration Strategy

Replace the legacy banners table with the new schema. Legacy data from the PHP site is not preserved.

## Files to Create/Modify

| File | Action |
|------|--------|
| `app/models/misc.py` | Replace `Banners` model with new schema |
| `app/schemas/banner.py` | New: `BannerResponse`, `BannerSize` enum |
| `app/api/v1/banners.py` | New: endpoints |
| `app/api/v1/__init__.py` | Register banner router |
| `app/config.py` | Add `BANNER_BASE_URL`, `BANNER_CACHE_TTL` |
| `app/services/banner.py` | New: caching logic |
| `alembic/versions/xxx_recreate_banners_table.py` | Migration |
| `tests/api/v1/test_banners.py` | New: endpoint tests |

## Out of Scope (v1)

- Admin CRUD endpoints for banner management
- User banner preferences (favorite/exclude, preferred size)
- Cache invalidation on banner changes
- Banner upload functionality (banners added manually to filesystem/DB)

## Dependencies

- Nginx config update (manual, outside codebase)
- Banner image files placed in `/shuushuu/banners/`
