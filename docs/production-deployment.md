# Production Deployment Guide - test.shuushuu.com

This guide covers deploying the Shuushuu application to production with the domain `test.shuushuu.com`.

## Prerequisites

- DNS already points `test.shuushuu.com` to your server's IP ✅
- Server running Ubuntu/Debian Linux
- Docker and Docker Compose installed
- Ports 80 and 443 accessible from the internet

## Step-by-Step Deployment

### 1. Open Firewall Ports

```bash
# Check if firewall is active
sudo ufw status

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

### 2. Obtain SSL Certificate (Let's Encrypt)

**Option A: Using Certbot on Host (Recommended)**

```bash
# Install certbot
sudo apt-get update
sudo apt-get install -y certbot

# Stop nginx if it's currently running on port 80
docker compose stop nginx

# Obtain certificate (standalone mode)
sudo certbot certonly --standalone -d test.shuushuu.com

# Certificates will be stored in:
# /etc/letsencrypt/live/test.shuushuu.com/fullchain.pem
# /etc/letsencrypt/live/test.shuushuu.com/privkey.pem
```

**Option B: Using Certbot with Webroot** (if you can't stop nginx)

```bash
# Install certbot
sudo apt-get install -y certbot

# Create webroot directory
sudo mkdir -p /var/www/certbot

# Obtain certificate using webroot
sudo certbot certonly --webroot -w /var/www/certbot -d test.shuushuu.com
```

### 3. Update Environment Variables

Edit `.env` file in `/home/dtaylor/shuu/shuushuu-api/`:

```bash
# Change environment to production
ENVIRONMENT=production

# Update CORS to include production domain
CORS_ORIGINS=https://test.shuushuu.com,https://www.test.shuushuu.com

# Ensure SECRET_KEY is set to a secure random value
# Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=your-actual-secure-random-secret-here

# Turn off debug mode
DEBUG=False
```

### 4. Update Docker Compose for Production

Edit `docker-compose.yml` to add SSL certificate volumes and update nginx ports:

```yaml
nginx:
  build:
    context: .
    dockerfile: Dockerfile.nginx
  container_name: shuushuu-nginx
  environment:
    - STORAGE_PATH=${STORAGE_PATH:-/shuushuu/images}
  volumes:
    # Mount storage path for nginx to serve images
    - ${STORAGE_PATH:-/shuushuu/images}:${STORAGE_PATH:-/shuushuu/images}:ro
    # Mount nginx configs
    - ./docker/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    # Use production config (or keep frontend.conf.template and switch manually)
    - ./docker/nginx/frontend-production.conf.template:/etc/nginx/conf.d/frontend.conf.template:ro
    # Mount SSL certificates
    - /etc/letsencrypt:/etc/letsencrypt:ro
    - /var/www/certbot:/var/www/certbot:ro
  ports:
    - "80:80"      # Changed from 3000:80
    - "443:443"    # Added HTTPS
  extra_hosts:
    - "host.docker.internal:host-gateway"
  depends_on:
    - api
  restart: unless-stopped
```

### 5. Build Frontend for Production

```bash
cd /home/dtaylor/shuu/shuushuu-frontend

# Build production assets
npm run build

# The built files will be in build/ directory
```

**Option A: Serve via Node adapter** (requires adding frontend service to docker-compose.yml)

**Option B: Continue using Vite dev server** (less ideal but works for testing)

### 6. Restart Services

```bash
cd /home/dtaylor/shuu/shuushuu-api

# Pull/rebuild images if needed
docker compose build nginx

# Restart all services
docker compose down
docker compose up -d

# Check logs
docker compose logs -f nginx
docker compose logs -f api
```

### 7. Test the Deployment

```bash
# Test HTTP redirect
curl -I http://test.shuushuu.com
# Should return 301/302 redirect to https://

# Test HTTPS
curl -I https://test.shuushuu.com
# Should return 200 OK

# Test API endpoint
curl https://test.shuushuu.com/api/v1/images/1111520
```

### 8. Set Up Certificate Auto-Renewal

```bash
# Test renewal process (dry run)
sudo certbot renew --dry-run

# Certbot automatically sets up a systemd timer for renewal
# Verify it's active:
sudo systemctl status certbot.timer

# To manually add a cron job (if timer doesn't exist):
sudo crontab -e
# Add this line:
# 0 0,12 * * * certbot renew --quiet --post-hook "docker compose -f /home/dtaylor/shuu/shuushuu-api/docker-compose.yml restart nginx"
```

## Production Checklist

- [ ] DNS points to server IP
- [ ] Firewall allows ports 80 and 443
- [ ] SSL certificates obtained and mounted
- [ ] `ENVIRONMENT=production` in `.env`
- [ ] `DEBUG=False` in `.env`
- [ ] `SECRET_KEY` is a secure random value
- [ ] `CORS_ORIGINS` includes production domain
- [ ] Docker Compose ports updated to 80/443
- [ ] Production nginx config is in use
- [ ] Frontend built for production
- [ ] Services restarted
- [ ] HTTPS works correctly
- [ ] Certificate auto-renewal configured

## Switching Between Development and Production

To switch configs without editing docker-compose.yml:

```bash
# For development
ln -sf frontend.conf.template docker/nginx/active.conf.template

# For production
ln -sf frontend-production.conf.template docker/nginx/active.conf.template

# Then update docker-compose.yml to use:
# - ./docker/nginx/active.conf.template:/etc/nginx/conf.d/frontend.conf.template:ro
```

## Troubleshooting

### Certificate Issues
```bash
# Check certificate expiry
sudo certbot certificates

# Force renewal
sudo certbot renew --force-renewal
```

### Nginx Errors
```bash
# Check nginx config syntax
docker compose exec nginx nginx -t

# View detailed logs
docker compose logs nginx --tail=100
```

### CORS Errors
- Ensure `CORS_ORIGINS` in `.env` includes `https://test.shuushuu.com`
- Check browser console for specific CORS errors
- Verify API is receiving correct `Origin` header

### 502 Bad Gateway
- Check if backend API is running: `docker compose ps api`
- View API logs: `docker compose logs api`
- Ensure API container is healthy

## Security Hardening (Recommended)

1. **Rate Limiting**: Already configured in FastAPI, but consider adding nginx rate limiting:
   ```nginx
   limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
   ```

2. **Fail2ban**: Set up fail2ban to block repeated failed login attempts

3. **Regular Updates**: Keep Docker images and system packages updated

4. **Monitoring**: Set up monitoring (Prometheus + Grafana) for production alerts

5. **Backups**: Regular database backups (automated with cron)
