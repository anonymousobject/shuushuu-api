# Unified SSR-through-Nginx Architecture

**Date:** 2025-12-30
**Status:** Implemented
**Problem:** CORS errors when promoting from dev to test environments

## Summary

Route all SSR requests through nginx, identical to browser requests. This eliminates CORS issues entirely since all requests are same-origin from the API's perspective.

## Architecture

```
BEFORE (broken in test):
┌─────────┐     ┌───────┐     ┌─────┐
│ Browser │────▶│ nginx │────▶│ API │  ✅ Works (same-origin)
└─────────┘     └───────┘     └─────┘

┌─────────┐                   ┌─────┐
│   SSR   │──────────────────▶│ API │  ❌ CORS error (cross-origin)
└─────────┘                   └─────┘

AFTER (unified):
┌─────────┐     ┌─────────────┐     ┌─────┐
│ Browser │────▶│ nginx :443  │────▶│ API │  ✅ Works (HTTPS)
└─────────┘     └─────────────┘     └─────┘

┌─────────┐     ┌─────────────┐     ┌─────┐
│   SSR   │────▶│ nginx :8080 │────▶│ API │  ✅ Works (internal HTTP)
└─────────┘     └─────────────┘     └─────┘
```

## Security: Internal Port 8080

The nginx internal server listens on port 8080, which is **NOT exposed** in docker-compose (only 80/443 are mapped). This prevents external clients from reaching the internal endpoint even by spoofing the `Host: nginx` header.

```yaml
# docker-compose.test.yml
nginx:
  ports:
    - "80:80"    # ACME challenges + redirect
    - "443:443"  # HTTPS (browser)
    # Port 8080 NOT exposed - internal only
```

The internal nginx server only exposes `/api/` and `/health` endpoints - no `location /` block to prevent circular proxy scenarios.

## Configuration

| Environment | `PUBLIC_API_URL` | SSR reaches nginx via |
|-------------|------------------|----------------------|
| Dev | `http://localhost:3000` | Host port mapping |
| Test | `http://nginx:8080` | Docker internal network |
| Prod | `http://nginx:8080` | Docker internal network |

## Implementation Details

### Frontend (client.ts)
- Uses `globalThis.fetch` on server-side to bypass SvelteKit's CORS enforcement
- SvelteKit's `event.fetch` enforces browser-like CORS even on server, breaking internal Docker communication
- Browser uses relative paths (empty string), proxied via nginx

```typescript
// On server-side, always use globalThis.fetch to avoid SvelteKit's CORS enforcement
const fetchFn = browser ? (customFetch ?? fetch) : globalThis.fetch;
```

### Frontend (.env.development)
```
PUBLIC_API_URL=http://localhost:3000
```

### Frontend (.env.test)
```
PUBLIC_API_URL=http://nginx:8080
```

### Frontend (health endpoint)
Added `/health` route (`src/routes/health/+server.ts`) for Docker health checks that doesn't trigger SSR logic.

### API (docker-compose.test.yml)
```yaml
frontend:
  build:
    args:
      - PUBLIC_API_URL=http://nginx:8080
  environment:
    - PUBLIC_API_URL=http://nginx:8080
```

### API (nginx/frontend.conf.test)
Internal server on port 8080:
```nginx
server {
    listen 8080;
    server_name nginx;  # Only matches Docker-internal requests

    # API proxy - the only endpoint SSR needs
    location /api/ {
        proxy_pass http://api:8000;
        # ... headers
    }

    # Health check
    location /health {
        return 200 "healthy\n";
    }
}
```

## Scaling Considerations

- nginx handles 10,000+ concurrent connections easily
- Extra hop adds ~1-2ms latency (negligible)
- Horizontal scaling works naturally via Docker/Kubernetes DNS
- No architectural changes needed for production scale

## Testing Checklist

- [x] Dev: Browser loads homepage
- [x] Dev: SSR works (view page source shows pre-rendered data)
- [x] Dev: HMR works (edit component, see live update)
- [x] Dev: Authentication flow works
- [x] Test: Health check passes (container shows healthy)
- [x] Test: No CORS errors in frontend logs
- [x] Test: SSR requests go through nginx:8080/api/
- [x] Test: Authentication flow works

## Rollback

Configuration-only change. Revert `.env` files and nginx config to restore previous behavior.
