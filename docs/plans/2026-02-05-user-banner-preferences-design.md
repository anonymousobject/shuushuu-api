# User Banner Preferences Design

## Overview

Allow logged-in users to customize banner display: choose a preferred size and pin favorite banners per size and theme. Anonymous users and users without preferences see a small rotating banner (current default behavior).

## Requirements

- Preferred banner size: defaults to small, user can change to large
- Per-size, per-theme pinned banners: users can pin a favorite banner for each size+theme combination (up to 4 pins: 2 sizes × 2 themes)
- Pinned banners always display (replace rotation entirely for that slot)
- Server-side resolution: the `/current` endpoint handles preference logic — frontend makes the same API call regardless of auth state
- Stale pins (inactive banners) fall through to normal rotation without deleting the pin

## Data Model

Two new tables. Preferences stores the size choice; pins stores per-slot favorites.

```python
class UserBannerPreferences(SQLModel, table=True):
    """One row per user — size preference only."""
    __tablename__ = "user_banner_preferences"

    user_id: int  # PK, FK to users
    preferred_size: BannerSize = BannerSize.small

class UserBannerPins(SQLModel, table=True):
    """One row per pin — up to 4 per user (2 sizes × 2 themes)."""
    __tablename__ = "user_banner_pins"

    user_id: int       # FK to users
    size: BannerSize
    theme: str         # "dark" | "light"
    banner_id: int     # FK to banners
    # UNIQUE(user_id, size, theme)
```

No row in `user_banner_preferences` means defaults apply (size=small, no pins). Most users will have 0-2 pins.

### Schema

```sql
CREATE TABLE user_banner_preferences (
    user_id INT PRIMARY KEY,
    preferred_size ENUM('small', 'large') NOT NULL DEFAULT 'small',
    CONSTRAINT fk_ubp_user FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE user_banner_pins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    size ENUM('small', 'large') NOT NULL,
    theme VARCHAR(5) NOT NULL,  -- 'dark' or 'light'
    banner_id INT NOT NULL,
    UNIQUE KEY uq_user_size_theme (user_id, size, theme),
    CONSTRAINT fk_ubpin_user FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_ubpin_banner FOREIGN KEY (banner_id) REFERENCES banners(banner_id)
        ON DELETE CASCADE ON UPDATE CASCADE
);
```

## API Changes

### Modified: GET /api/v1/banners/current

- `theme` (required): dark | light
- `size` (optional, default: small): small | large
- If authenticated with preferences, `preferred_size` overrides the `size` query param
- If a pin exists for (user_id, effective_size, theme) and the banner is active, return it
- Otherwise, fall through to normal cached rotation

### New: GET /api/v1/banners/preferences (requires auth)

Returns the user's preferences with pinned banners inlined:

```json
{
  "preferred_size": "small",
  "pins": [
    { "size": "small", "theme": "dark", "banner": { "banner_id": 12, "name": "Winter", ... } },
    { "size": "small", "theme": "light", "banner": { "banner_id": 7, "name": "Spring", ... } }
  ]
}
```

Returns defaults (`preferred_size: "small"`, empty pins) if no preferences row exists.

### New: PATCH /api/v1/banners/preferences (requires auth)

Update size preference:

```json
{ "preferred_size": "large" }
```

Creates the preferences row if it doesn't exist (upsert).

### New: PUT /api/v1/banners/preferences/pins/{size}/{theme} (requires auth)

Pin a banner for a specific slot:

```json
{ "banner_id": 12 }
```

Validates: banner exists, is active, matches the given size and theme. Upserts the pin row.

### New: DELETE /api/v1/banners/preferences/pins/{size}/{theme} (requires auth)

Removes the pin for that slot. Returns 404 if no pin exists.

## Server-Side Resolution Flow

```
Request: GET /banners/current?theme=dark&size=small (+ optional auth)
│
├─ Authenticated?
│   ├─ Query user_banner_preferences for user_id
│   ├─ effective_size = preferred_size (or default small if no row)
│   ├─ Query user_banner_pins for (user_id, effective_size, theme)
│   │   ├─ Pin exists → fetch banner → active? → return it
│   │   └─ No pin or inactive → fall through ↓
│   └─ Shared rotation cache: banner:current:{theme}:{effective_size}
│
└─ Anonymous?
    └─ Shared rotation cache: banner:current:{theme}:{size param}
```

## Caching

- The shared rotation cache (4 keys) is unchanged — all non-pinned users share it
- Pinned banner lookups hit the DB (PK lookup, cheap). Skip per-user caching in v1.
- Preference/pin updates don't require cache invalidation for the shared cache

## Edge Cases

- **Pinned banner deactivated:** Pin row stays (banner may be reactivated). Server checks `active` at query time, falls through to rotation if inactive. `GET /preferences` includes the pin but the frontend can check the banner's `active` field.
- **Pinned banner deleted (CASCADE):** Pin row is deleted automatically via FK cascade.
- **User deleted (CASCADE):** Both preferences and pins rows deleted automatically.

## Testing

### Unit tests
- Model validation for UserBannerPreferences and UserBannerPins
- Pin validation: banner must exist, be active, match size and theme

### API tests
- `GET /current` — anonymous defaults to small + rotation
- `GET /current` — authenticated with preferred size uses that size
- `GET /current` — authenticated with pin returns pinned banner
- `GET /current` — pin on inactive banner falls through to rotation
- `GET /preferences` — defaults when no row exists
- `GET /preferences` — preferences with inlined pins
- `PATCH /preferences` — updates preferred size
- `PUT /pins/{size}/{theme}` — creates pin, validates banner matches
- `PUT /pins/{size}/{theme}` — rejects mismatched/inactive/nonexistent banner
- `DELETE /pins/{size}/{theme}` — removes pin, 404 if absent

### Integration tests
- Full flow: set size → pin banner → `/current` returns pinned
- Pin active banner → deactivate → `/current` falls through to rotation

## Files to Create/Modify

| File | Action |
|------|--------|
| `app/models/misc.py` | Add UserBannerPreferences, UserBannerPins models |
| `app/schemas/banner.py` | Add preference/pin request/response schemas |
| `app/services/banner.py` | Add preference lookup, modify get_current_banner |
| `app/api/v1/banners.py` | Add preference/pin endpoints, modify /current |
| `alembic/versions/xxx_add_user_banner_preferences.py` | Migration |
| `tests/unit/test_banner_preferences.py` | Unit tests |
| `tests/api/v1/test_banner_preferences.py` | API tests |
| `tests/integration/test_banner_preferences.py` | Integration tests |

## Out of Scope

- Per-user caching of preferences (optimize later if needed)
- Admin endpoints for managing other users' preferences
- Excluding specific banners from rotation
- Banner upload/CRUD (banners still managed manually)
