# Unified SSR-through-Nginx Architecture

**Date:** 2025-12-30
**Status:** Approved
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
┌─────────┐     ┌───────┐     ┌─────┐
│ Browser │────▶│ nginx │────▶│ API │  ✅ Works
└─────────┘     └───────┘     └─────┘

┌─────────┐     ┌───────┐     ┌─────┐
│   SSR   │────▶│ nginx │────▶│ API │  ✅ Works (same path)
└─────────┘     └───────┘     └─────┘
```

## Configuration

| Environment | `PUBLIC_API_URL` | SSR reaches nginx via |
|-------------|------------------|----------------------|
| Dev | `http://localhost:3000` | Host port mapping |
| Test | `http://nginx` | Docker container name |
| Prod | `http://nginx` | Docker container name |

## Changes Required

### Frontend (.env.development)
```diff
- PUBLIC_API_URL=http://localhost:8000
+ PUBLIC_API_URL=http://localhost:3000
```

### Frontend (.env.test)
```diff
- PUBLIC_API_URL=http://api:8000
+ PUBLIC_API_URL=http://nginx
```

### Frontend (hooks.server.ts)
- Remove separate `SERVER_API_URL` variable
- Use `API_BASE_URL` from `$lib/api/client` for consistency

### Frontend (client.ts)
- Update documentation comments to reflect unified approach

### API (docker-compose.test.yml)
```diff
  frontend:
    build:
      args:
-       - PUBLIC_API_URL=http://api:8000
+       - PUBLIC_API_URL=http://nginx
    environment:
-     - PUBLIC_API_URL=http://api:8000
+     - PUBLIC_API_URL=http://nginx
```

### API (main.py)
- Revert any CORS middleware changes from debugging session

## Scaling Considerations

- nginx handles 10,000+ concurrent connections easily
- Extra hop adds ~1-2ms latency (negligible)
- Horizontal scaling works naturally via Docker/Kubernetes DNS
- No architectural changes needed for production scale

## Testing Checklist

- [ ] Dev: Browser loads homepage
- [ ] Dev: SSR works (view page source shows pre-rendered data)
- [ ] Dev: HMR works (edit component, see live update)
- [ ] Dev: Authentication flow works
- [ ] Test: Health check passes (container shows healthy)
- [ ] Test: No CORS errors in frontend logs
- [ ] Test: Authentication flow works

## Rollback

Configuration-only change. Revert `.env` files to restore previous behavior.
