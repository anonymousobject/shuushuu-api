# HTTPS Deployment Guide for test.shuushuu.com

Since test.shuushuu.com is internet-exposed, HTTPS is required for security and browser compatibility.

## Overview

The system uses:
- **Nginx** - Reverse proxy handling HTTPS, HTTP→HTTPS redirect, and Let's Encrypt challenges
- **Certbot** - Automated certificate renewal via Let's Encrypt
- **FastAPI** - Backend API (no direct HTTPS needed)
- **SvelteKit** - Dev server proxied through nginx

## Setup Steps

### 1. Create Required Directories

```bash
mkdir -p docker/certbot/conf
mkdir -p docker/certbot/www
```

### 2. Configure Environment Variables

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` and set these for **production** (test.shuushuu.com):

```bash
# CRITICAL: Must change for production!
ENVIRONMENT=production
SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")  # Generate secure key

# HTTPS domain (match your DNS)
CORS_ORIGINS=https://test.shuushuu.com
FRONTEND_URL=https://test.shuushuu.com
IMAGE_BASE_URL=https://test.shuushuu.com

# Database - use container hostname
DATABASE_URL=mysql+aiomysql://shuushuu:change_this_password@mariadb:3306/shuushuu?charset=utf8mb4

# Other settings...
```

### 3. Obtain SSL Certificate (First Time)

Before starting services, get a Let's Encrypt certificate:

```bash
# Stop nginx if running
docker compose stop nginx

# Run certbot standalone mode
docker compose run --rm certbot certonly --standalone -d test.shuushuu.com

# When prompted:
# - Enter email for renewal notices
# - Agree to terms (A)
# - No DNS CNAME (N)
```

This creates certificates in `docker/certbot/conf/live/test.shuushuu.com/`:
- `fullchain.pem` - Full certificate chain
- `privkey.pem` - Private key

### 4. Start Services

```bash
# Start all containers
docker compose up -d

# Verify nginx is running
docker compose logs nginx -f

# Test HTTPS
curl -I https://test.shuushuu.com  # Should show 200 OK (after auth)
```

## How It Works

### HTTP → HTTPS Flow

1. **Client accesses** `http://test.shuushuu.com`
2. **Nginx (port 80)** receives request
3. **Redirect** returns 301 to `https://test.shuushuu.com`
4. **Client retries** with HTTPS
5. **Nginx (port 443)** receives request
6. **SSL/TLS handshake** completes with Let's Encrypt cert
7. **Basic auth** prompt appears (if configured)
8. **Proxy** forwards to backend/frontend

### Automatic Certificate Renewal

Certbot runs continuously and:
- Checks for renewal every 12 hours
- Renewals happen 30 days before expiration
- On successful renewal, executes deploy hook: `docker exec shuushuu-nginx nginx -s reload`
- Nginx reloads config to use new certificates

## Troubleshooting

### Nginx Won't Start

```bash
# Check config syntax
docker compose exec nginx nginx -t

# View detailed logs
docker compose logs nginx --tail=50
```

**Common Issues:**
- Certificate files don't exist: Run certbot as described above
- Certificate path typo: Check `/etc/letsencrypt/live/test.shuushuu.com/` exists in container
- Port already in use: `sudo lsof -i :443`

### Certificate Errors in Browser

**"NET::ERR_CERT_AUTHORITY_INVALID"**
- Certificate hasn't been issued yet: run certbot
- Wrong domain: `openssl s_client -connect test.shuushuu.com:443 -servername test.shuushuu.com`

**"Certificate has expired"**
- Certbot not renewing: Check if `certbot` service is running: `docker compose ps certbot`
- View renewal logs: `docker compose logs certbot`

### Image URLs Are Broken

**Symptom:** Images show 404 or load from wrong domain

**Fix:** Ensure `IMAGE_BASE_URL` in `.env` matches the public domain:
```bash
# For internet-exposed test.shuushuu.com:
IMAGE_BASE_URL=https://test.shuushuu.com

# Not:
IMAGE_BASE_URL=http://localhost:3000
```

### CORS Errors

**Symptom:** "CORS policy: No 'Access-Control-Allow-Origin' header"

**Fix:** Update `CORS_ORIGINS` in `.env`:
```bash
# For HTTPS domain:
CORS_ORIGINS=https://test.shuushuu.com
```

Restart API:
```bash
docker compose restart api
```

## Security Checklist

- [ ] `SECRET_KEY` is a unique random value (not the default)
- [ ] `CORS_ORIGINS` uses HTTPS and matches your domain
- [ ] `IMAGE_BASE_URL` uses HTTPS for public domain
- [ ] Database password is changed from default
- [ ] `.env` file is in `.gitignore` (never commit secrets)
- [ ] Firewall allows ports 80 (HTTP) and 443 (HTTPS)
- [ ] DNS points `test.shuushuu.com` to your server IP

## Production Deployment (Future)

When moving to real production:

1. **Use a production certificate provider** (Let's Encrypt is free, automatic)
2. **Enable firewall** to only allow needed ports
3. **Set DEBUG=False** in `.env`
4. **Use strong SECRET_KEY** (generate with `secrets` module)
5. **Monitor certificate renewal** with external monitoring
6. **Set up log aggregation** for debugging
7. **Consider CDN** for static assets (images)
8. **Enable HSTS** preload (already in nginx config)

## File Structure

```
docker/
├── certbot/
│   ├── conf/               # ← Certificates stored here
│   │   └── live/test.shuushuu.com/
│   │       ├── fullchain.pem
│   │       └── privkey.pem
│   └── www/                # ← ACME challenge files
├── nginx/
│   ├── frontend.conf.template    # ← Main nginx config (HTTPS)
│   ├── nginx.conf
│   └── .htpasswd           # ← Basic auth credentials
```

## Related Files

- `docker-compose.yml` - Certbot and nginx service definitions
- `docker/nginx/frontend.conf.template` - Nginx configuration
- `app/main.py` - CORS middleware setup
- `app/config.py` - URL configuration defaults
