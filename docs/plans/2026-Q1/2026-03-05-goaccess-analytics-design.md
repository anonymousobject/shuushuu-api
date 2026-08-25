# GoAccess Web Analytics Setup

## Goal

Track traffic patterns for shuushuu using GoAccess to analyze nginx access logs. Provides real-time dashboards and periodic summary reports covering page analytics, visitor demographics, referrers, and geographic data.

## Context

- Production host runs nginx directly (not containerized), proxying to Docker containers (FastAPI on :8000, SvelteKit on :3000)
- Nginx already uses `combined` log format, which GoAccess supports natively
- Datadog agent handles basic system metrics; this fills the gap for web traffic analytics
- GoAccess was chosen over Umami/Plausible because it requires zero app code changes, captures all traffic (including bots and direct API calls), and is lightweight

## Components

### 1. GoAccess Installation

Install GoAccess with GeoIP support from the package manager or official repo.

```bash
# Ubuntu/Debian — use the official GoAccess repo for the latest version
echo "deb [signed-by=/usr/share/keyrings/goaccess.gpg] https://deb.goaccess.io/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/goaccess.list
wget -O - https://deb.goaccess.io/gnupg.key | gpg --dearmor | sudo tee /usr/share/keyrings/goaccess.gpg >/dev/null
sudo apt update && sudo apt install goaccess
```

### 2. MaxMind GeoLite2 Database

Required for geographic visitor data (country/city).

1. Register at https://www.maxmind.com/en/geolite2/signup
2. Generate a license key in the account portal
3. Install `geoipupdate` to auto-refresh the database:

```bash
sudo apt install geoipupdate
```

4. Configure `/etc/GeoIP.conf`:

```ini
AccountID YOUR_ACCOUNT_ID
LicenseKey YOUR_LICENSE_KEY
EditionIDs GeoLite2-City GeoLite2-Country GeoLite2-ASN
```

5. Run `sudo geoipupdate` and add a weekly cron job:

```bash
# /etc/cron.d/geoipupdate
0 3 * * 3 root /usr/bin/geoipupdate
```

Database files land in `/usr/share/GeoIP/` by default.

### 3. GoAccess Configuration

Create `/etc/goaccess/goaccess.conf` (or use a custom path):

```conf
# Log format — matches nginx combined
log-format COMBINED

# Real-time HTML
real-time-html true
ws-url wss://YOUR_DOMAIN:443/analytics/ws

# Output
output /var/www/goaccess/index.html

# GeoIP
geoip-database /usr/share/GeoIP/GeoLite2-City.mmdb

# Nginx log path
log-file /var/log/nginx/access.log

# Persist parsed data across restarts
db-path /var/lib/goaccess/
persist true
restore true

# Ignore crawlers in visitor count (optional, they still appear in their own panel)
ignore-crawlers false

# Exclude the analytics dashboard itself from stats
exclude-ip 127.0.0.1
# Add your own IP if desired:
# exclude-ip YOUR_IP

# Keep data for 12 months
keep-last 365
```

### 4. Systemd Service

Create `/etc/systemd/system/goaccess.service`:

```ini
[Unit]
Description=GoAccess Real-Time Web Analytics
After=nginx.service

[Service]
Type=simple
ExecStart=/usr/bin/goaccess /var/log/nginx/access.log \
    --config-file=/etc/goaccess/goaccess.conf \
    --real-time-html \
    --output=/var/www/goaccess/index.html
ExecStartPre=/bin/mkdir -p /var/www/goaccess /var/lib/goaccess
Restart=on-failure
RestartSec=5
User=www-data
Group=adm

[Install]
WantedBy=multi-user.target
```

Notes:
- `Group=adm` grants read access to `/var/log/nginx/access.log` (nginx logs are typically owned by `root:adm`)
- Verify the log file permissions on your host; adjust the group if needed

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now goaccess
```

### 5. Nginx Configuration

Expose the dashboard behind basic auth. Add to your site's server block:

```nginx
# GoAccess analytics dashboard
location /analytics {
    alias /var/www/goaccess;
    index index.html;

    # Basic auth — only you should see this
    auth_basic "Analytics";
    auth_basic_user_file /etc/nginx/.htpasswd_analytics;

    # Alternative: IP allowlist instead of/in addition to basic auth
    # allow YOUR_IP;
    # deny all;
}

# WebSocket for real-time updates
location /analytics/ws {
    proxy_pass http://127.0.0.1:7890;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Same auth as the dashboard
    auth_basic "Analytics";
    auth_basic_user_file /etc/nginx/.htpasswd_analytics;
}
```

Create the htpasswd file:

```bash
sudo apt install apache2-utils  # if not already installed
sudo htpasswd -c /etc/nginx/.htpasswd_analytics admin
```

### 6. Periodic Reports (Cron)

Generate a static HTML snapshot daily for historical reference:

```bash
# /etc/cron.d/goaccess-reports
0 0 * * * www-data /usr/bin/goaccess /var/log/nginx/access.log \
    --config-file=/etc/goaccess/goaccess.conf \
    --output=/var/www/goaccess/reports/report-$(date +\%Y-\%m-\%d).html \
    --no-real-time 2>/dev/null
```

Create the reports directory:

```bash
sudo mkdir -p /var/www/goaccess/reports
sudo chown www-data:www-data /var/www/goaccess/reports
```

Optionally add a cleanup cron to remove reports older than 90 days:

```bash
0 1 * * * root find /var/www/goaccess/reports -name "*.html" -mtime +90 -delete
```

### 7. Log Rotation Consideration

Nginx typically rotates logs via logrotate. GoAccess with `persist`/`restore` handles this well, but verify that your logrotate config sends a USR1 signal to nginx (standard behavior) so GoAccess can pick up the new log file. If GoAccess stops updating after rotation, add a `postrotate` hook to restart the goaccess service:

```
postrotate
    systemctl restart goaccess
endscript
```

## What the Dashboard Shows

- Unique visitors per day/hour
- Requested pages and endpoints (shows which images, searches, API calls are popular)
- Static files (thumbnails, full-size images)
- HTTP status codes (404s, 5xx errors)
- Referrers (where traffic comes from)
- User agents, browsers, operating systems
- Geographic location (country and city)
- Bandwidth consumed
- Time distribution (peak hours)
- Bot/crawler traffic

## Implementation Order

1. Install GoAccess
2. Register MaxMind and set up GeoIP
3. Create GoAccess config file
4. Create and start systemd service
5. Add nginx location block and basic auth
6. Verify dashboard works at `https://YOUR_DOMAIN/analytics`
7. Set up cron for daily reports
8. (Optional) Tune exclusions, add your IP to exclude list, etc.
