# Production Release Implementation Plan (Revised)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy shuushuu-api to production at e-shuushuu.net, replacing the PHP image board while preserving wiki/forums. Host nginx is the edge proxy; Docker runs the application services only.

**Architecture:** Host nginx handles SSL, bot blocking, wiki/forums (PHP-FPM), and proxies the new site to Docker containers on localhost. Docker Compose overlay runs api, frontend, arq-worker, redis, and iqdb. MariaDB is on a remote dedicated server.

**Tech Stack:** Host nginx, Docker Compose, FastAPI/Uvicorn, Redis, Arq, SvelteKit, IQDB-RS, MariaDB (remote), PHP-FPM (wiki/forums)

**Design doc:** `docs/plans/2026-02-15-production-release-design.md`

---

## Phase 1: Update Docker Compose for Host-Nginx Architecture

### Task 1: Update `docker-compose.prod.yml`

Remove the nginx and certbot services, expose api and frontend on localhost, and update the frontend's `PUBLIC_API_URL` to connect directly to the API container.

**Files:**
- Modify: `docker-compose.prod.yml`

**Step 1: Remove the nginx service override**

Delete the entire `nginx:` block (lines 55-70 in current file):
```yaml
  nginx:
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./docker/nginx/frontend-production.conf.template:/etc/nginx/conf.d/frontend.conf.template:ro
      - ./docker/certbot/conf:/etc/letsencrypt:ro
      - ./docker/certbot/www:/var/www/certbot:ro
    environment:
      - NGINX_HOST=${DOMAIN}
      - NGINX_PORT=443
    depends_on:
      frontend:
        condition: service_healthy
    restart: unless-stopped
    logging: *default-logging
```

Replace with a disabled profile (same pattern as mariadb):
```yaml
  nginx:
    profiles: [disabled]
```

**Step 2: Remove the certbot service**

Delete the entire `certbot:` block (lines 117-142) and replace with:
```yaml
  certbot:
    profiles: [disabled]
```

**Step 3: Update frontend `PUBLIC_API_URL`**

Change the frontend's `PUBLIC_API_URL` from `http://nginx:8080` to `http://api:8000` in both the build args and environment sections. Without Docker nginx, the frontend SSR connects directly to the API container on the Docker network.

Change both occurrences:
```yaml
# Before:
        - PUBLIC_API_URL=http://nginx:8080
# After:
        - PUBLIC_API_URL=http://api:8000
```

**Step 4: Expose API and frontend on localhost**

The API needs port 8000 and frontend needs port 3000 accessible from the host (for host nginx to proxy to).

For `api`, change `ports: []` to:
```yaml
    ports:
      - "127.0.0.1:8000:8000"
```

For `frontend`, add ports (it doesn't have any currently since it was accessed via Docker nginx):
```yaml
    ports:
      - "127.0.0.1:3000:3000"
```

Both bind to `127.0.0.1` only — not accessible from the internet, only from the host nginx.

**Step 5: Update the comment at the top of the file**

Change:
```yaml
# Production override - HTTPS with Let's Encrypt
# Usage: docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d
```
To:
```yaml
# Production override — host nginx as edge proxy
# Docker provides: api, frontend, arq-worker, redis, iqdb
# Host provides: nginx (SSL, wiki/forums), certbot, PHP-FPM
# Usage: docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d
```

**Step 6: Verify the compose config parses**

Run:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod config --quiet 2>&1 || true
```
Expected: Exit 0 (warnings about unfilled placeholders are OK).

**Step 7: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "refactor: use host nginx as edge proxy, remove Docker nginx/certbot

Remove nginx and certbot from production Docker stack. Host nginx
handles SSL, bot blocking, wiki/forums, and proxies to Docker
containers on localhost. Frontend SSR connects directly to API
container (http://api:8000). API and frontend exposed on 127.0.0.1
only."
```

---

## Phase 2: Host Nginx Configuration

### Task 2: Create the production host nginx config

Create a new nginx config that replaces the PHP image board rules with proxy rules to Docker containers while preserving wiki/forums, SSL, and bot blocking.

**Files:**
- Create: `docker/nginx/e-shuushuu.net.conf` (tracked in repo, deployed to server)
- Reference: `/etc/nginx/sites-enabled/e-shuushuu.net.conf` (current live config)
- Reference: `docker/nginx/frontend-production.conf.template` (image-serving patterns)

**Step 1: Create the production host nginx config**

The config preserves everything from the current live config that isn't PHP-image-board-specific, and adds the Docker proxy rules. Image serving patterns come from `frontend-production.conf.template` but with `http://127.0.0.1:8000` instead of `http://api:8000` (host networking, not Docker networking) and hardcoded paths instead of `${STORAGE_PATH}` variables (host nginx doesn't use envsubst).

```nginx
# Production nginx config for e-shuushuu.net
# Serves: FastAPI/SvelteKit (via Docker), MediaWiki, phpBB
#
# Docker containers (localhost only):
#   - API:      127.0.0.1:8000
#   - Frontend: 127.0.0.1:3000
#
# To deploy: sudo cp docker/nginx/e-shuushuu.net.conf /etc/nginx/sites-enabled/e-shuushuu.net.conf
# Test first: sudo nginx -t
# Reload:     sudo systemctl reload nginx
# Backup:     sudo cp /etc/nginx/sites-enabled/e-shuushuu.net.conf /etc/nginx/sites-enabled/e-shuushuu.net.conf.bak

upstream shuu-php8-fpm-sock {
  server unix:/var/run/php/shuu-php-fpm.sock;
}

server {
  listen 443 ssl http2;
  listen [::]:443 ssl http2;
  server_name e-shuushuu.net;

  ssl_certificate /etc/letsencrypt/live/e-shuushuu.net/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/e-shuushuu.net/privkey.pem;

  access_log /var/log/nginx/e-shuushuu.net.access.log;
  error_log /var/log/nginx/e-shuushuu.net.error.log;

  root /var/www/e-shuushuu.net;

  client_max_body_size 100M;
  gzip on;

  # Security headers
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  add_header X-Frame-Options "SAMEORIGIN" always;
  add_header X-Content-Type-Options "nosniff" always;

  # Block .git access
  location ~ /\.git {
    deny all;
  }

  # =========================================================================
  # Wiki & Forums — PHP-FPM (unchanged)
  # =========================================================================

  location ~ (wiki\/.*\.php|forums\/.*\.php) {
    limit_req zone=wiki-forums-global;

    fastcgi_intercept_errors on;
    fastcgi_pass             shuu-php8-fpm-sock;
    include                  fastcgi_params;

    fastcgi_cache            shuucache;
    fastcgi_cache_valid      200 10m;
    fastcgi_cache_bypass     $cookie_session;
  }

  # Static files for wiki/forums (css, js, images, etc.)
  location /wiki/ {
    try_files $uri $uri/ =404;
  }

  location /forums/ {
    try_files $uri $uri/ =404;
  }

  # =========================================================================
  # Shuushuu API — proxy to Docker (127.0.0.1:8000)
  # =========================================================================

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  # =========================================================================
  # Image serving — permission check via API + X-Accel-Redirect from disk
  # =========================================================================

  # Protected image routes — proxy to FastAPI for permission checks
  location ~ "^/images/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9]+\.(png|jpg|jpeg|gif|webp)$" {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ~ "^/thumbs/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9]+\.(png|jpg|jpeg|gif|webp)$" {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ~ "^/medium/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9]+\.(png|jpg|jpeg|gif|webp)$" {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location ~ "^/large/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9]+\.(png|jpg|jpeg|gif|webp)$" {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  # Internal locations — only accessible via X-Accel-Redirect from FastAPI
  location /internal/fullsize/ {
    internal;
    alias /shuushuu/images/fullsize/;
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  location /internal/thumbs/ {
    internal;
    alias /shuushuu/images/thumbs/;
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  location /internal/medium/ {
    internal;
    alias /shuushuu/images/medium/;
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  location /internal/large/ {
    internal;
    alias /shuushuu/images/large/;
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  # Avatars — no protection needed, serve directly
  location /images/avatars/ {
    alias /shuushuu/images/avatars/;
    expires 30d;
    add_header Cache-Control "public, immutable";
  }

  # Banners — no protection needed, serve directly
  location /images/banners/ {
    alias /shuushuu/images/banners/;
    expires 7d;
    add_header Cache-Control "public, immutable";
  }

  # =========================================================================
  # Legacy PHP URL redirects
  # =========================================================================

  location ~ "^/image/([0-9]+)/?$" {
    return 301 /images/$1;
  }

  # =========================================================================
  # Frontend — proxy to SvelteKit SSR (127.0.0.1:3000)
  # =========================================================================

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Increase buffer sizes for large SvelteKit modulepreload headers
    proxy_buffer_size 16k;
    proxy_buffers 4 16k;
    proxy_busy_buffers_size 32k;
  }

  # =========================================================================
  # Status & monitoring
  # =========================================================================

  location /health {
    access_log off;
    add_header Content-Type text/plain;
    return 200 "healthy\n";
  }

  location /nginx_status {
    satisfy any;
    allow 127.0.0.1;
    deny all;
    stub_status on;
    access_log off;
  }

  # =========================================================================
  # Bot blocking & SSL
  # =========================================================================

  include /etc/nginx/bots.d/ddos.conf;
  include /etc/nginx/bots.d/blockbots.conf;

  include certbot-e-shuushuu.net.conf;
}

# Redirect HTTP to HTTPS
server {
  listen 80;
  server_name e-shuushuu.net;

  # Allow Let's Encrypt ACME challenges
  location /.well-known/acme-challenge/ {
    root /var/www/e-shuushuu.net;
  }

  location / {
    return 301 https://e-shuushuu.net$request_uri;
  }
}

# Redirect www to non-www
server {
  listen 80;
  server_name www.e-shuushuu.net;
  return 301 https://e-shuushuu.net$request_uri;
}
```

**Step 2: Validate nginx config syntax (offline check)**

The config can't be tested with `nginx -t` yet (it would conflict with the live config). But verify there are no obvious syntax issues by checking that the file is well-formed.

**Step 3: Commit**

```bash
git add docker/nginx/e-shuushuu.net.conf
git commit -m "feat: add production host nginx config for e-shuushuu.net

Replaces PHP image board with proxy rules to Docker containers.
Preserves wiki/forums PHP-FPM, SSL, bot blocking, and certbot.
API on 127.0.0.1:8000, frontend on 127.0.0.1:3000."
```

---

## Phase 3: Update .env.prod

### Task 3: Remove CERT_PATH from `.env.prod`

The `CERT_PATH` variable was for Docker certbot/nginx. Since host nginx handles SSL directly, this variable is no longer needed.

**Files:**
- Modify: `.env.prod`

**Step 1: Remove the CERT_PATH lines**

Delete:
```
# SSL Certificate paths
CERT_PATH=/etc/letsencrypt/live/e-shuushuu.net
```

This is a gitignored file so no commit is needed.

---

## Phase 4: Cutover Checklist (Manual)

These are manual steps for the operator during the maintenance window. Documented here for reference.

### Task 4: Pre-cutover validation

**Step 1: Fill in .env.prod placeholders**

Replace all `<PLACEHOLDER>` values in `.env.prod` with real credentials.

**Step 2: Re-sync image symlinks**

Re-run the symlink script to ensure any images uploaded since the initial run are linked. The script is idempotent — existing symlinks are skipped.

```bash
SRC_BASE=/path/to/php/images ./scripts/create_prod_symlinks.sh --apply
```

**Step 3: Generate missing thumbnails**

Generate WebP thumbnails for all images that don't have one yet.

```bash
uv run scripts/generate_thumbnails.py --missing-only --all
```

**Step 4: Prune inactive users**

Remove users inactive for 180+ days (legacy PHP accounts with no activity).

```bash
uv run scripts/prune_inactive_users.py --days-inactive 180 --confirm
```

**Step 5: Build Docker images**

```bash
make prod-build
```

**Step 6: Start Docker stack**

```bash
make prod-up
```

**Step 7: Verify containers are running**

```bash
make prod-ps
```

**Step 8: Test API health on localhost**

```bash
curl -s http://127.0.0.1:8000/health
```
Expected: JSON health response.

**Step 9: Test frontend on localhost**

```bash
curl -s http://127.0.0.1:3000/health
```
Expected: Health response from SvelteKit.

### Task 5: Execute cutover

**Step 1: Backup current nginx config**

```bash
sudo cp /etc/nginx/sites-enabled/e-shuushuu.net.conf /etc/nginx/sites-enabled/e-shuushuu.net.conf.php-backup
```

**Step 2: Deploy new nginx config**

```bash
sudo cp docker/nginx/e-shuushuu.net.conf /etc/nginx/sites-enabled/e-shuushuu.net.conf
```

**Step 3: Test and reload**

```bash
sudo nginx -t && sudo systemctl reload nginx
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`

**Step 4: Verify site**

```bash
curl -s -o /dev/null -w "%{http_code}" https://e-shuushuu.net/
curl -s -o /dev/null -w "%{http_code}" https://e-shuushuu.net/api/v1/meta/health
curl -s -o /dev/null -w "%{http_code}" https://e-shuushuu.net/wiki/
curl -s -o /dev/null -w "%{http_code}" https://e-shuushuu.net/forums/
```
Expected: All return 200.

### Task 6: Backout (if needed)

```bash
# 1. Restore original nginx config
sudo cp /etc/nginx/sites-enabled/e-shuushuu.net.conf.php-backup /etc/nginx/sites-enabled/e-shuushuu.net.conf
sudo systemctl reload nginx

# 2. Stop Docker containers
make prod-down
```
