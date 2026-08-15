# Security Audit - shuushuu-api

**Date:** 2026-02-12
**Scope:** Full codebase (FastAPI backend)
**Approach:** Automated tooling + manual review

---

## Phase 1: Automated Tooling Results

### pip-audit
No known CVEs in Python dependencies.

### trivy (filesystem scan)

| Severity | Finding | Status |
|----------|---------|--------|
| **HIGH** | `pillow 12.0.0` - CVE-2026-25990: Out-of-bounds Write via crafted PSD image. Fixed in 12.1.1 | TODO |
| **HIGH** | Both Dockerfiles run as root (no `USER` instruction) | TODO |
| **HIGH** | Dockerfile missing `--no-install-recommends` on apt-get | TODO |
| LOW | Both Dockerfiles missing `HEALTHCHECK` | TODO |

### semgrep (291 rules, 93 files)

| Severity | File | Finding | Status |
|----------|------|---------|--------|
| WARNING | `app/api/v1/auth.py:200` | SHA1 hash (legacy password verification) | Known/accepted |
| WARNING | `app/main.py:120` | Wildcard CORS `*` (development-only, gated by env check) | Known/accepted |

### bandit (real findings only, noise excluded)

| Severity | File | Finding | Status |
|----------|------|---------|--------|
| **HIGH** | `app/api/v1/auth.py:200` | SHA1 for legacy password verification | Known/accepted |
| **HIGH** | `app/services/avatar.py:158` | MD5 for avatar filename generation (not security use) | False positive |
| **HIGH** | `app/services/image_processing.py:169` | MD5 for file content hashing (not security use) | False positive |
| LOW (x8) | Various | `assert` for mypy type narrowing | See Finding M3 |

Excluded noise: "bcrypt"/"bearer"/"test-token" flagged as hardcoded passwords, `random` for banner selection, try/except/pass patterns.

---

## Phase 2: Manual Review

### Area 1: Authentication Flow

**Files reviewed:** `app/api/v1/auth.py`, `app/core/auth.py`, `app/core/security.py`, `app/models/refresh_token.py`, `app/schemas/auth.py`, `app/core/permission_deps.py`

#### M1: Refresh token exposed in JSON response body (MEDIUM)

**Location:** `app/api/v1/auth.py:346-351`, `app/api/v1/auth.py:508-513`

`TokenResponse` returns the refresh token in the JSON body alongside setting it as an HTTPOnly cookie. This undermines HTTPOnly protection - if any XSS vulnerability exists, an attacker could intercept the login/refresh API response and extract the 30-day refresh token from the body.

The SvelteKit frontend proxies all API calls server-to-server. The browser never sees the JSON body - login uses form actions (browser gets 303 redirect) and token refresh runs in server hooks. SvelteKit reads the token from the JSON body and re-sets it as its own HTTPOnly cookie. This is the correct pattern for separate backend/frontend services where the backend's Set-Cookie headers can't be forwarded directly to the browser. This still may need mitigation.

**Recommendation:** Remove `refresh_token` from the response body if SSR can rely on the cookie being set by the browser. If SSR needs it, consider a separate server-only endpoint.

#### M2: Refresh token cookie not path-scoped (LOW)

**Location:** `app/api/v1/auth.py:150-157`

No `path` parameter on `set_cookie`, so the refresh token cookie is sent on every request to the domain. Should be scoped to `/api/v1/auth` to minimize exposure.

**Recommendation:** Add `path="/api/v1/auth"` to the refresh token cookie.

#### M3: `assert` used in auth dependency chain (LOW)

**Location:** `app/core/auth.py:101`, `app/core/permission_deps.py:171`

`assert` statements for security-relevant checks would be silently stripped if Python runs with `-O` flag. Unlikely in practice for FastAPI apps, but violates defense-in-depth.

**Recommendation:** Replace with explicit `if not ...: raise ValueError(...)`.

#### M4: No rate limiting on password change attempts (LOW)

**Location:** `app/api/v1/auth.py:591`

`/change-password` verifies the current password with no brute-force protection. An attacker with a stolen session could attempt unlimited current-password guesses. Mitigated by 10-min access token expiry, but refresh token extends the window.

**Recommendation:** Add rate limiting (e.g., 5 attempts per 15 minutes per user).

#### M5: Race condition window in reuse detection (INFO)

**Location:** `app/api/v1/auth.py:420`

10-second grace window for concurrent page loads means an attacker who uses a stolen token within 10 seconds of a legitimate refresh gets a soft 401 instead of triggering family revocation. Reasonable UX trade-off, low risk.

#### Auth positives
- Refresh tokens stored as SHA256 hashes, never plaintext
- Token family tracking with reuse detection
- Account lockout (5 attempts -> 15 min)
- Suspension checked on both login AND token refresh
- bcrypt 12 rounds with proper long-password handling (SHA256 pre-hash for >72 bytes)
- Cookie flags: HTTPOnly, Secure (prod), SameSite=strict
- JWT verifies signature, expiration, and token type claim
- Password change revokes all sessions

### Area 2: Permission System, Business Logic & Access Control

**Files reviewed:** `app/api/v1/images.py`, `app/api/v1/users.py`, `app/api/v1/comments.py`, `app/api/v1/privmsgs.py`, `app/api/v1/media.py`, `app/core/permissions.py`, `app/core/permission_deps.py`, `app/services/image_visibility.py`, `app/schemas/user.py`

#### M6: `GET /users/{user_id}/images` missing visibility filter (MEDIUM)

**Location:** `app/api/v1/users.py:580-634`

This public endpoint returns ALL images uploaded by a user with no status filtering. Compare to `list_images` (images.py:141) which applies `PUBLIC_IMAGE_STATUSES` filtering for anonymous/regular users. Any anonymous user can see deactivated, pending, or otherwise hidden images by browsing to `/users/{user_id}/images`.

**Recommendation:** Apply the same visibility filtering as `list_images` - filter to `PUBLIC_IMAGE_STATUSES` for anonymous users, allow owners to see their own, and allow users with IMAGE_EDIT/REVIEW_VIEW permission to see all.

#### M7: `GET /users/{user_id}/favorites` missing visibility filter (LOW)

**Location:** `app/api/v1/users.py:679-731`

Same issue as M6. Returns favorited images regardless of image visibility status. Could reveal that hidden/removed images exist.

**Recommendation:** Join with Images and filter by `PUBLIC_IMAGE_STATUSES` or apply the same visibility logic.

#### M8: Upload error leaks exception details to client (LOW)

**Location:** `app/api/v1/images.py:1914-1917`

```python
detail=f"Failed to upload image: {str(e)}"
```

The generic exception handler exposes the raw exception string, which could contain internal file paths, database connection strings, or other sensitive details.

**Recommendation:** Return a generic error message. Log the full exception server-side (which is already done on line 1903).

#### M9: `send_privmsg` doesn't validate recipient exists (LOW)

**Location:** `app/api/v1/privmsgs.py:54-77`

No check that `to_user_id` is a valid, active user. The FK constraint prevents saving to a non-existent user, but results in a 500 error instead of a clean 404. More importantly, no check prevents sending messages to suspended/inactive users.

**Recommendation:** Validate recipient exists and is active before creating the message.

#### M10: `getattr` for sort column selection (INFO)

**Location:** `app/api/v1/comments.py:139`, `app/api/v1/users.py:1083`

Uses `getattr(Model, sorting.sort_by)` to select sort columns. This is safe as long as the sort params are validated by Pydantic (constraining to a whitelist of valid column names). If validation were removed or bypassed, this could access unintended model attributes.

**Recommendation:** No action needed if sort params are validated (they appear to be). Document the dependency on Pydantic validation.

#### Permission system & access control positives
- RBAC with group + direct user permissions, Redis-cached
- Permission checks consistently applied to destructive operations (delete, status changes)
- Owner/admin/permission three-tier check pattern applied consistently for image operations
- `UserUpdate` schema properly restricts updateable fields
- `user_title` and `maximgperday` require explicit permission (USER_EDIT_PROFILE)
- Private messages properly check sender/recipient ownership
- Comment ownership verified for edit/delete operations
- Moderator comment deletion logged to admin audit trail
- Media serving (X-Accel-Redirect) uses DB-sourced filenames, not user input (path traversal safe)
- Image visibility service properly separates public/owner/moderator tiers
- Comment reports use Redis lock to prevent race conditions on duplicates
- Registration has honeypot, Turnstile CAPTCHA, and IP-based rate limiting

### Area 3: File Upload Chain

**Files reviewed:** `app/api/v1/images.py` (upload_image), `app/services/upload.py`, `app/services/image_processing.py` (from earlier exploration)

No additional findings beyond what automated tools flagged. Upload chain is well-implemented:
- PIL `verify()` validates actual image content (prevents disguised malicious files)
- Allowlist for file extensions (jpg, jpeg, png, gif)
- 32MB file size limit
- Rate limiting (30s between uploads, skip for admins)
- MD5 duplicate detection
- IQDB near-duplicate detection with confirmation flow
- Filenames generated server-side (date-prefix + image_id), no user-controlled path components
- Temp file cleanup on error

### Area 4: Input Handling

**Files reviewed:** `app/utils/markdown.py`, `app/schemas/user.py`, `app/api/v1/comments.py`, `app/api/v1/images.py`

No XSS findings. The markdown parser escapes ALL HTML before processing safe markdown syntax. Plain text fields rely on Svelte's safe template interpolation on the frontend (defense-in-depth). User search LIKE patterns don't escape `%` and `_` wildcards (allows unintended pattern matching, but not a security issue - just unexpected behavior).

### Area 5: Summary of all findings by severity

| ID | Severity | Finding | Area |
|----|----------|---------|------|
| T1 | **HIGH** | Pillow CVE-2026-25990 (OOB write via PSD) | Dependency |
| T2 | **HIGH** | Dockerfiles run as root | Infrastructure |
| M1 | **MEDIUM** | Refresh token in JSON response body | Auth |
| M6 | **MEDIUM** | `/users/{id}/images` missing visibility filter | Access control |
| M2 | LOW | Refresh token cookie not path-scoped | Auth |
| M3 | LOW | `assert` in auth chain | Auth |
| M4 | LOW | No rate limit on password change | Auth |
| M7 | LOW | `/users/{id}/favorites` missing visibility filter | Access control |
| M8 | LOW | Upload error leaks exception details | Information leak |
| M9 | LOW | PM recipient not validated | Input validation |
| T3 | LOW | Dockerfile missing `--no-install-recommends` | Infrastructure |
| M5 | INFO | Refresh token reuse 10s grace window | Auth |
| M10 | INFO | `getattr` sort column pattern | Code quality |
