# Production Release Design — e-shuushuu.net

## Context

Deploy shuushuu-api and its dependencies to the production server, replacing the existing PHP image board at e-shuushuu.net. The server also hosts MediaWiki (`/wiki/`) and phpBB (`/forums/`) which must continue working. The MariaDB database lives on a separate, dedicated server. The existing PHP database will be migrated to a new schema alongside the original (which is preserved for rollback).

## Architecture

### Deployment Model

Host nginx remains the edge proxy, handling SSL, bot blocking, and routing. It serves wiki/forums via PHP-FPM directly and proxies the new FastAPI/SvelteKit traffic to Docker containers on localhost.

Docker Compose overlay for the application services:
```
docker-compose.yml (base) + docker-compose.prod.yml (overlay) + .env.prod
```

### Request Routing (host nginx)

```
Host nginx (ports 80/443, SSL, bot blocking)
├── /wiki/*          → PHP-FPM (unchanged)
├── /forums/*        → PHP-FPM (unchanged)
├── /api/*           → Docker: localhost:8000 (FastAPI)
├── /images/YYYY-*   → Docker: localhost:8000 (permission check) + X-Accel-Redirect
├── /thumbs/YYYY-*   → Docker: localhost:8000 + X-Accel-Redirect
├── /medium/YYYY-*   → Docker: localhost:8000 + X-Accel-Redirect
├── /large/YYYY-*    → Docker: localhost:8000 + X-Accel-Redirect
├── /images/avatars/ → Serve directly from disk
├── /images/banners/ → Serve directly from disk
├── /image/{id}      → 301 redirect to /images/{id} (legacy PHP URLs)
└── /*               → Docker: localhost:3000 (SvelteKit SSR)
```

### Docker Services

| Service    | Purpose                              | Port (localhost) |
|------------|--------------------------------------|------------------|
| api        | FastAPI app (uvicorn, production)    | 8000             |
| arq-worker | Background job processor             | none             |
| frontend   | SvelteKit SSR                        | 3000             |
| redis      | Cache (db 0) + task queue (db 1)     | none             |
| iqdb       | Image similarity search              | none             |

### Removed from Docker (vs. earlier design)

| Service          | Reason                                          |
|------------------|-------------------------------------------------|
| mariadb          | Remote dedicated server                         |
| nginx            | Host nginx handles edge routing                 |
| certbot          | Host certbot already manages certificate renewal|
| adminer          | Not needed in production                        |
| redis-commander  | Not needed in production                        |

### External Connections

- API and arq-worker connect to remote MariaDB over the network via `DATABASE_URL`
- API connects to SMTP via postfix on host (`host.docker.internal`)
- Frontend SSR connects to API directly via Docker network (`http://api:8000`)

### Makefile

`COMPOSE_PROD` and targets: `prod`, `prod-up`, `prod-down`, `prod-logs`, `prod-ps`, `prod-build`.

## Host Nginx Config

The existing `/etc/nginx/sites-enabled/e-shuushuu.net.conf` is modified.

### Kept as-is
- SSL config and certbot include
- Bot blocking includes (`/etc/nginx/bots.d/`)
- Wiki/forums PHP-FPM location blocks
- HTTP -> HTTPS redirect
- www -> non-www redirect
- `client_max_body_size`, gzip

### Removed
- PHP image board locations (generic `\.php` block, image rewrites, PHP error pages)
- FastCGI caching for the image board
- PHP-FPM status/nginx status blocks (optional — can keep if desired)

### Added
- Proxy to Docker frontend (`/` -> `localhost:3000`)
- Proxy to Docker API (`/api/` -> `localhost:8000`)
- Protected image routes proxied to API for permission checks
- X-Accel-Redirect internal locations for image serving from disk
- Direct serving of avatars and banners
- Legacy URL redirect (`/image/{id}` -> `/images/{id}`)
- Security headers (HSTS, X-Frame-Options, X-Content-Type-Options)

### Location Priority

Wiki/forums regex locations match specific paths and take priority over the catch-all `location /` that proxies to the frontend.

## Database Migration

### Strategy

Create a new database (`shuushuu_v2`) alongside the existing PHP database on the remote MariaDB server. The PHP database is never modified.

### Steps

1. Create `shuushuu_v2` on the remote MariaDB server
2. Dump the PHP database and import into `shuushuu_v2`
3. Run `scripts/migrate_legacy_db.py` against `shuushuu_v2`
4. Run Alembic migrations to bring schema to current
5. After cutover, rebuild the IQDB index using the separate IQDB rebuild script

### .env.prod Database Config

```
DATABASE_URL=mysql+aiomysql://shuushuu:password@db-server-ip:3306/shuushuu_v2?charset=utf8mb4
DATABASE_URL_SYNC=mysql+pymysql://shuushuu:password@db-server-ip:3306/shuushuu_v2?charset=utf8mb4
```

## Image Files & Storage

### Directory Structure

```
/shuushuu/images/
├── fullsize/    # Original uploads
├── thumbs/      # WebP thumbnails (generated)
├── medium/      # Medium variants
├── large/       # Large variants
├── avatars/     # User avatars
└── banners/     # Banner images
```

### Strategy: Symlinks

Due to disk space constraints, the new directory structure uses symlinks pointing to the existing PHP site's image files. This:

- Costs zero additional disk space
- Keeps the PHP site's files intact for rollback
- Can be done entirely before cutover
- Symlinks can be gradually replaced with real file moves post-cutover when convenient

A production adaptation of `~/sync_from_prod.sh` creates the symlink tree, handling directory flattening (deactivated subfolder) and filename normalization (stripping `-medium`/`-large` suffixes).

## SSL/TLS & Domain

### Existing Infrastructure (unchanged)

- SSL certificates at `/etc/letsencrypt/live/e-shuushuu.net/` managed by host certbot
- Certbot auto-renewal already configured on the host
- No Docker certbot container needed

### .env.prod Domain Config

```
DOMAIN=e-shuushuu.net
FRONTEND_URL=https://e-shuushuu.net
IMAGE_BASE_URL=https://e-shuushuu.net
CORS_ORIGINS=https://e-shuushuu.net
```

## Logging

Docker json-file log driver with rotation, configured via a YAML anchor in `docker-compose.prod.yml`:

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"
```

Applied to all services. Per-service retention: 250MB (5 x 50MB). Total worst case across 5 services: ~1.25GB.

Host nginx continues logging to `/var/log/nginx/` as before.

## Cutover Sequence

### Pre-Cutover (days/weeks before)

1. Create `.env.prod` and `docker-compose.prod.yml`
2. Prepare the new host nginx config (keep original as backup)
3. Create `shuushuu_v2` database on remote MariaDB
4. Dump PHP database, import into `shuushuu_v2`
5. Run `migrate_legacy_db.py` against `shuushuu_v2`
6. Run Alembic migrations against `shuushuu_v2`
7. Build symlink tree for images pointing to existing files
8. Build Docker images (`make prod-build`)
9. Start Docker stack on localhost ports, verify API/frontend health

### Cutover (maintenance window)

10. Announce maintenance
11. **Full database backup** — dump the entire PHP database to a timestamped file on the DB server
12. Re-sync new data from PHP database to `shuushuu_v2` (or fresh dump + migrate if simpler)
13. Start Docker stack (`make prod-up`) if not already running
14. Swap host nginx config (replace PHP image board rules with Docker proxy rules)
15. `sudo nginx -t && sudo systemctl reload nginx` — near-instant cutover
16. Rebuild IQDB index (separate script)
17. Verify: site loads, images serve, login works, search works, wiki works, forums work

### Post-Cutover

- Monitor logs (`make prod-logs` and `/var/log/nginx/`)
- Gradually replace symlinks with real file moves when convenient
- Old PHP database stays in place as a safety net

## Backout Plan

### Immediate Rollback (minutes)

1. Restore the original nginx config (kept as backup)
2. `sudo systemctl reload nginx` — PHP image board is back
3. `make prod-down` — stop Docker containers

Wiki and forums are never affected.

### Why This Works

- PHP database is untouched (we created `shuushuu_v2` alongside it)
- Image files are untouched (symlinks point to originals, nothing moved)
- Host nginx config was backed up before modification
- DNS unchanged (same server, same IP)
- Wiki/forums never go down (served by host nginx throughout)
- Full database backup from start of maintenance window available for restore

### Point of No Return

The deployment becomes harder to roll back once:

- New user registrations/uploads are accepted (data only in `shuushuu_v2`)
- Symlinks are replaced with real file moves
- The old PHP database is dropped

Until those actions, rollback is a config-swap + nginx reload.
