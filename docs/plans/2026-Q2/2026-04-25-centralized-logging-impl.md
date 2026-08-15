# Centralized Logging Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Loki + Grafana Alloy + Grafana on the prod host, ingesting all Docker container logs and host nginx access/error logs, queryable via SSH-tunneled Grafana with 60-day retention.

**Architecture:** Three new localhost-bound Docker services in `docker-compose.prod.yml`. Alloy auto-discovers containers via the Docker socket and tails host nginx files. Loki stores chunks on the local filesystem in single-binary mode with TSDB indexing. Grafana provides a read-only LogQL UI on `127.0.0.1:3001`. No application code changes.

**Tech Stack:** Grafana Alloy (log shipper, successor to Promtail), Loki (single-binary, filesystem storage, TSDB schema v13), Grafana (LogQL UI), Docker Compose.

**Reference spec:** `docs/plans/2026-Q2/2026-04-25-centralized-logging-design.md`

**Implementation deviation from spec:** Section 4 of the spec uses Docker `secrets:` for the Grafana admin password. This plan uses an env var sourced from `.env.prod` instead, matching the project's existing secret-handling pattern (DATABASE_URL, MARIADB_PASSWORD, etc. already follow this). Functionally equivalent; no new infra to introduce.

---

## File Structure

**New files:**
- `docker/alloy/config.alloy` — Alloy flow-language config (Docker discovery + nginx file source + JSON parsing + Loki write)
- `docker/loki/loki-config.yaml` — Loki single-binary config (TSDB schema, retention, limits)
- `docker/grafana/provisioning/datasources/loki.yml` — Grafana Loki datasource provision
- `docs/log-operations.md` — operator runbook (SSH tunnel, sample LogQL queries, retention test procedure)

**Modified files:**
- `docker-compose.prod.yml` — three new services (alloy, loki, grafana), three new named volumes
- `.env.prod.example` (if it exists; otherwise documented separately) — add `GRAFANA_ADMIN_PASSWORD` placeholder
- `docs/logging-guide.md` — one-line cross-reference at top, pointing to `log-operations.md`

**Touched on prod host (manual, outside repo):**
- `.env.prod` — add `GRAFANA_ADMIN_PASSWORD=<generated>` (gitignored already)

---

## Chunk 1: Bootstrap & inspection

Goal: gather information needed for downstream config (frontend log format), set up the directory skeleton, ensure secrets handling is ready. Ends in a commit of empty-but-tracked config directories with an updated `.env.prod.example`.

### Task 1.1: Inspect frontend container log format

**Files:** none modified; investigation only.

This is the spec's deferred decision. Frontend log parsing depends on what the container actually emits.

- [ ] **Step 1: Bring up prod stack on the prod host (or capture sample on dev)**

If running on the prod host:
```bash
cd /path/to/shuushuu-api
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod ps frontend
```

Confirm `shuushuu-frontend-prod` is running.

- [ ] **Step 2: Capture 100 lines of frontend output**

```bash
docker logs --tail 100 shuushuu-frontend-prod 2>&1 | tee /tmp/frontend-sample.log
```

- [ ] **Step 3: Classify the format**

Inspect `/tmp/frontend-sample.log`. Decide which case applies:

| Case | Indicator | Alloy parser |
|---|---|---|
| Plain text | No `{` JSON; lines like `2026-04-25 14:30:01 GET /` | No `stage.json`; pass through, `level=info` fallback |
| JSON | Lines start with `{` and parse as JSON | `stage.json` extracting `level`, `msg` |
| Mixed (e.g., SvelteKit's default + occasional JSON) | Some `{...}`, some plain | `stage.match` to branch; JSON path for matched, raw for rest |

Record the decision in the next step's commit message and as a code comment in `config.alloy` later.

- [ ] **Step 4: Commit the captured sample as evidence (optional)**

If the format is non-obvious, save a redacted sample to `docs/log-operations.md` (created in Chunk 5). For now, record the decision in the implementation log:

```bash
mkdir -p /tmp/log-impl
cp /tmp/frontend-sample.log /tmp/log-impl/frontend-sample.log
```

(Not committed to repo. Used as reference when writing Alloy config in Chunk 3.)

### Task 1.2: Create the config directory skeleton

**Files:**
- Create: `docker/alloy/.gitkeep`
- Create: `docker/loki/.gitkeep`
- Create: `docker/grafana/provisioning/datasources/.gitkeep`

- [ ] **Step 1: Create directories**

```bash
mkdir -p docker/alloy docker/loki docker/grafana/provisioning/datasources
touch docker/alloy/.gitkeep docker/loki/.gitkeep docker/grafana/provisioning/datasources/.gitkeep
```

- [ ] **Step 2: Verify**

```bash
find docker/alloy docker/loki docker/grafana -type f
```

Expected output:
```
docker/alloy/.gitkeep
docker/loki/.gitkeep
docker/grafana/provisioning/datasources/.gitkeep
```

### Task 1.3: Add Grafana admin password to env example

**Files:**
- Modify: `.env.prod.example` (if it exists; check first)

- [ ] **Step 1: Check whether `.env.prod.example` exists**

```bash
ls .env*example 2>&1
```

If it exists, edit it. If not, document the required env var in the new `docs/log-operations.md` in Chunk 5 instead, and skip to Step 3.

- [ ] **Step 2: Append to `.env.prod.example`**

Add at the end:
```
# Grafana admin password (centralized logging UI)
# Generate with: openssl rand -base64 24
GRAFANA_ADMIN_PASSWORD=changeme
```

- [ ] **Step 3: On the prod host, add to `.env.prod`**

(Manual step, not committed.)
```bash
# On prod host:
echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)" >> .env.prod
```

### Task 1.4: Commit chunk 1

- [ ] **Step 1: Stage and commit**

```bash
git add docker/alloy/.gitkeep docker/loki/.gitkeep docker/grafana/provisioning/datasources/.gitkeep
git add .env.prod.example  # if modified
git commit -m "chore(logging): scaffold dirs for loki/alloy/grafana stack"
```

---

## Chunk 2: Loki + Grafana running, datasource verified

Goal: bring up Loki and Grafana with no agent. Verify Grafana can reach Loki and the datasource health check passes. End state: Grafana Explore opens but shows "no logs" because Alloy is not yet running.

### Task 2.1: Write Loki configuration

**Files:**
- Create: `docker/loki/loki-config.yaml`

- [ ] **Step 1: Write the failing verification first**

Verification (cannot run yet — Loki container does not exist):
```bash
docker exec shuushuu-loki-prod wget -qO- http://localhost:3100/ready
```
Expected eventually: `ready`. Now: command not found / container missing.

- [ ] **Step 2: Create `docker/loki/loki-config.yaml`**

```yaml
# Loki single-binary config for shuushuu-api production logging.
# See docs/plans/2026-Q2/2026-04-25-centralized-logging-design.md section 3.

auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9095
  log_level: info

common:
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    instance_addr: 127.0.0.1
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2026-04-25
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: /loki/index
    cache_location: /loki/cache

limits_config:
  retention_period: 60d
  ingestion_rate_mb: 16
  ingestion_burst_size_mb: 32
  max_streams_per_user: 5000
  max_query_length: 30d
  max_query_lookback: 60d
  reject_old_samples: true
  reject_old_samples_max_age: 168h  # 7d, prevents agent backfill abuse

compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
  compaction_interval: 10m
  retention_delete_delay: 2h
  retention_delete_worker_count: 150

# No analytics phone-home
analytics:
  reporting_enabled: false
```

### Task 2.2: Write Grafana datasource provisioning

**Files:**
- Create: `docker/grafana/provisioning/datasources/loki.yml`

- [ ] **Step 1: Write the file**

```yaml
# Provisioned at first Grafana boot. editable: false enforces config-as-code.
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    uid: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
    editable: false
    jsonData:
      maxLines: 5000
      timeout: 60
```

### Task 2.3: Add Loki and Grafana services to prod compose

**Files:**
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Add the new services and volumes**

Open `docker-compose.prod.yml`. Add the following two services (place after the `iqdb` service block, before `certbot`):

```yaml
  loki:
    image: grafana/loki:3.3.2
    container_name: shuushuu-loki-prod
    user: "10001:10001"  # Loki's default uid; must match volume ownership
    command: -config.file=/etc/loki/loki-config.yaml
    ports: !override []  # Internal only
    volumes:
      - ./docker/loki/loki-config.yaml:/etc/loki/loki-config.yaml:ro
      - loki_data:/loki
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3100/ready"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    logging: *default-logging

  grafana:
    image: grafana/grafana:11.4.0
    container_name: shuushuu-grafana-prod
    ports: !override
      - "127.0.0.1:3001:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
      - GF_AUTH_ANONYMOUS_ENABLED=false
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_ANALYTICS_REPORTING_ENABLED=false
      - GF_ANALYTICS_CHECK_FOR_UPDATES=false
      - GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
    env_file: .env.prod
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/grafana/provisioning:/etc/grafana/provisioning:ro
    depends_on:
      loki:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:3000/api/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    logging: *default-logging
```

- [ ] **Step 2: Add the new volumes**

In the `volumes:` block at the bottom of `docker-compose.prod.yml`, add:

```yaml
  loki_data:
    name: loki_data_prod
    driver: local
  grafana_data:
    name: grafana_data_prod
    driver: local
```

(Alloy's volume comes in Chunk 3.)

### Task 2.4: Bring up Loki and Grafana, verify connectivity

- [ ] **Step 1: Start the new services**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d loki grafana
```

- [ ] **Step 2: Wait for healthy state and verify Loki readiness**

```bash
# Wait up to 60s
for i in {1..12}; do
  if docker exec shuushuu-loki-prod wget -qO- http://localhost:3100/ready 2>/dev/null | grep -q ready; then
    echo "Loki ready"; break
  fi
  sleep 5
done
docker exec shuushuu-loki-prod wget -qO- http://localhost:3100/ready
```

Expected: `ready` printed.

- [ ] **Step 3: Verify Grafana datasource health from inside the Grafana container**

```bash
# Use Grafana's datasource health API. Loki is provisioned with uid=loki.
curl -s -u "admin:${GRAFANA_ADMIN_PASSWORD}" http://127.0.0.1:3001/api/datasources/uid/loki/health
```

Expected: JSON response with `"status":"OK"` and message about successful Loki query.

- [ ] **Step 4: Open the UI via SSH tunnel (manual)**

From your laptop:
```bash
ssh -L 3001:localhost:3001 <prod-user>@<prod-host>
```
Then in browser: `http://localhost:3001`. Log in as `admin` / value of `GRAFANA_ADMIN_PASSWORD`. Navigate to Connections → Data sources → Loki → "Save & test" should be green. Explore tab opens with Loki selected; running `{host="prod"}` returns no streams (expected — no agent yet, so no labels exist).

### Task 2.5: Commit chunk 2

- [ ] **Step 1: Commit**

```bash
git add docker/loki/loki-config.yaml \
        docker/grafana/provisioning/datasources/loki.yml \
        docker-compose.prod.yml
git commit -m "feat(logging): add loki + grafana services for centralized log storage and UI"
```

---

## Chunk 3: Alloy ingesting Docker container logs

Goal: Alloy is up, watching the Docker socket, shipping container stdout to Loki with proper labels. End state: `{service="api"}` returns api request logs in Grafana within 5 seconds of a curl.

### Task 3.1: Write Alloy configuration — container log path only

**Files:**
- Create: `docker/alloy/config.alloy`

- [ ] **Step 1: Write the initial Alloy config**

This config covers ONLY the Docker container path. The host nginx file source is added in Chunk 4 to keep the verification cycle tight.

```alloy
// Grafana Alloy config for shuushuu-api production logging.
// See docs/plans/2026-Q2/2026-04-25-centralized-logging-design.md sections 1 and 2.
//
// Two log sources:
//   1. Docker containers via socket discovery (this file)
//   2. Host nginx files (added in Chunk 4)

logging {
  level  = "info"
  format = "logfmt"
}

// ---------- Loki write target ----------

loki.write "default" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
  external_labels = {
    host = "prod",
  }
}

// ---------- Docker container discovery ----------

discovery.docker "containers" {
  host             = "unix:///var/run/docker.sock"
  refresh_interval = "10s"
}

// Map container metadata into the labels we want.
// Container name like "/shuushuu-api-prod" becomes service="api".
discovery.relabel "containers" {
  targets = discovery.docker.containers.targets

  // Strip leading slash from container name
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "container_name"
  }

  // Derive `service` label by stripping shuushuu- prefix and -prod suffix
  rule {
    source_labels = ["container_name"]
    regex         = "shuushuu-(.*)-prod"
    target_label  = "service"
  }

  // Drop containers that don't match our naming convention (e.g. one-off debug containers)
  rule {
    source_labels = ["service"]
    regex         = ".+"
    action        = "keep"
  }

  // Carry through compose project label for multi-project hosts
  rule {
    source_labels = ["__meta_docker_container_label_com_docker_compose_project"]
    target_label  = "compose_project"
  }
}

// ---------- Container log source ----------

loki.source.docker "containers" {
  host       = "unix:///var/run/docker.sock"
  targets    = discovery.relabel.containers.output
  forward_to = [loki.process.containers.receiver]
  labels     = {}  // labels come from relabel above
}

// ---------- Container log processing ----------

loki.process "containers" {
  forward_to = [loki.write.default.receiver]

  // Try to parse JSON (structlog from api, arq-worker).
  // For non-JSON lines, this stage is a no-op and `level` stays empty.
  stage.json {
    expressions = {
      level     = "level",
      logger    = "logger",
      timestamp = "timestamp",
    }
  }

  // Default `level` to "info" when JSON parsing didn't extract one
  // (non-structlog services like redis, iqdb, frontend plain text).
  stage.template {
    source   = "level"
    template = "{{ if .Value }}{{ .Value }}{{ else }}info{{ end }}"
  }

  // Single labels stage promotes level once, after the default has been applied.
  stage.labels {
    values = {
      level = "",
    }
  }

  // Drop very high-volume noise: healthcheck pings.
  // Adjust patterns as observed in real traffic.
  stage.match {
    selector = "{service=\"api\"} |= \"GET /health\""
    action   = "drop"
  }
}
```

> **Note on flow syntax:** Pin Alloy to the version below; if config syntax differs in a future Alloy release, the executor should consult `https://grafana.com/docs/alloy/latest/` and adjust. The structure above (discovery → relabel → source → process → write) is stable across recent Alloy versions; only field names may vary.

### Task 3.2: Add Alloy service to prod compose

**Files:**
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Add Alloy service**

Append after the `grafana:` service block:

```yaml
  alloy:
    image: grafana/alloy:v1.5.1
    container_name: shuushuu-alloy-prod
    # Alloy's image default user is uid 473 (`alloy`); /var/lib/alloy/data is owned by 473.
    # We keep that uid and use group_add for the supplementary groups Alloy needs:
    #   docker — to read /var/run/docker.sock (gid varies; check with getent on prod)
    #   adm    — to read /var/log/nginx/* (added in Chunk 4)
    user: "473:473"
    group_add:
      - "999"  # docker group on prod; CHANGE if `getent group docker | cut -d: -f3` returns different
    command: >
      run
      --server.http.listen-addr=0.0.0.0:12345
      --storage.path=/var/lib/alloy/data
      /etc/alloy/config.alloy
    ports: !override []  # Internal only
    volumes:
      - ./docker/alloy/config.alloy:/etc/alloy/config.alloy:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - alloy_data:/var/lib/alloy/data
    depends_on:
      loki:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:12345/-/ready || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    logging: *default-logging
```

- [ ] **Step 2: Add `alloy_data` volume**

In the `volumes:` block at the bottom:

```yaml
  alloy_data:
    name: alloy_data_prod
    driver: local
```

- [ ] **Step 3: Confirm host docker group gid**

On the prod host:
```bash
getent group docker | cut -d: -f3
```
If output is not `999`, update `group_add:` in the compose file accordingly before bringing the service up.

### Task 3.3: Bring up Alloy, verify container logs flow

- [ ] **Step 1: Start Alloy**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d alloy
```

- [ ] **Step 2: Confirm Alloy reaches a healthy state**

```bash
docker ps --filter name=shuushuu-alloy-prod --format '{{.Status}}'
# Expect: "Up XX seconds (healthy)" within ~60s
docker logs --tail 50 shuushuu-alloy-prod
# Look for: "starting up", no panic/fatal lines
```

- [ ] **Step 3: Generate API traffic**

```bash
curl -s http://127.0.0.1:8000/api/v1/images/1111520 > /dev/null
# Or any known-good API call against the prod stack
```

- [ ] **Step 4: Verify the line lands in Loki**

Loki is not exposed on a host port (`ports: !override []`), so we query through Grafana's datasource proxy. `date +%s%N` is GNU-specific; this assumes the prod host is Linux (not macOS).

```bash
curl -sG -u "admin:${GRAFANA_ADMIN_PASSWORD}" \
  "http://127.0.0.1:3001/api/datasources/proxy/uid/loki/loki/api/v1/query_range" \
  --data-urlencode 'query={service="api"}' \
  --data-urlencode "start=$(date -u -d '5 minutes ago' +%s%N)" \
  --data-urlencode 'limit=5' | jq '.data.result | length'
```

Expected: a number ≥ 1.

- [ ] **Step 5: Verify structured JSON parsing**

In the Grafana UI (Explore tab, Loki selected), run:
```logql
{service="api"} | json
```
Expected: log lines display extracted fields like `request_id`, `elapsed_ms`, `event`, `level` in the column list. If `level` is also a label (not just a parsed field), the JSON pipeline worked end-to-end.

- [ ] **Step 6: Verify label cardinality is sane**

```logql
count by (service, level) (count_over_time({host="prod"}[5m]))
```
Expected: ≤ 30 rows. If hundreds, a relabel rule is leaking high-cardinality data into a label.

### Task 3.4: Verify frontend + iqdb logs flow, then tune parsing if needed

**Files:**
- Possibly modify: `docker/alloy/config.alloy` (only if Task 1.1 found a non-trivial format)

The container processing block in Task 3.1 is **safe-by-default**: any line lands in Loki regardless of format. Lines that aren't JSON simply don't get the structlog fields extracted; they still ship with `service`, `level=info`, `host`, `compose_project` labels and the raw message. So no edit is required for the frontend or iqdb to start showing up — only for *better parsing*.

- [ ] **Step 1: Generate frontend and iqdb traffic**

```bash
curl -s http://127.0.0.1:3000/ > /dev/null  # frontend
curl -s http://127.0.0.1:5588/status > /dev/null 2>&1 || true  # iqdb
```

- [ ] **Step 2: Confirm both flow through to Loki**

In Grafana Explore:
```logql
{service="frontend"}
```
Expected: ≥ 1 line within 5s.

```logql
{service="iqdb"}
```
Expected: ≥ 1 line within 5s (may be sparser if iqdb is idle).

If either returns nothing after 30s of generated traffic, **stop and debug** before proceeding — likely causes: container name doesn't match the `shuushuu-(.*)-prod` regex, container has no stdout output, or Alloy is not running. Check `docker logs shuushuu-alloy-prod`.

- [ ] **Step 3: Decide whether to add custom parsing per Task 1.1's finding**

Recall the Task 1.1 classification:

| Task 1.1 case | Action here |
|---|---|
| Frontend emits JSON | No change. The existing `stage.json` already extracts `level`, `logger`, `timestamp`. |
| Frontend emits plain text | No change required; the safe default treats it as text with `level=info`. Skip Step 4. |
| Frontend has a structured non-JSON format you want to extract | Proceed to Step 4. |

iqdb is plain text by design (per spec). No iqdb-specific parsing needed.

- [ ] **Step 4: (Conditional) Add a service-specific regex parser**

Only if Step 3 case is "structured non-JSON for frontend". Add a new `loki.process` block in `config.alloy` (do NOT modify the existing `loki.process "containers"`). Pattern:

```alloy
// Example: parse a frontend log line like "2026-04-25 14:30:01 [info] GET /"
loki.process "frontend_extra" {
  forward_to = [loki.write.default.receiver]

  // Filter to only frontend lines
  stage.match {
    selector = "{service=\"frontend\"}"
    stages   = [
      {
        regex = {
          expression = "^\\S+ \\S+ \\[(?P<level>\\w+)\\] (?P<msg>.*)",
        },
      },
      {
        labels = {
          values = { level = "" },
        },
      },
    ]
  }
}
```

Then change the existing `loki.source.docker "containers"` block's `forward_to` to include both processors:
```alloy
  forward_to = [loki.process.containers.receiver, loki.process.frontend_extra.receiver]
```

This adds custom parsing without disturbing the safe baseline.

- [ ] **Step 5: (If Step 4 was performed) Reload Alloy and re-verify**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod restart alloy
```

In Grafana, check that frontend lines now show the extracted `level` label and that no lines went missing:
```logql
count_over_time({service="frontend"}[5m])
```
Compare before/after. The number should not decrease.

### Task 3.5: Commit chunk 3

- [ ] **Step 1: Commit**

```bash
git add docker/alloy/config.alloy docker-compose.prod.yml
git commit -m "feat(logging): add alloy agent for docker container log ingestion"
```

---

## Chunk 4: Alloy ingesting host nginx logs

Goal: extend Alloy with a file source for `/var/log/nginx/*.{access,error}.log`. End state: `{service="nginx", vhost="e-shuushuu.net"}` returns access log entries within 5 seconds of an HTTP request through the public edge.

### Task 4.1: Add nginx file source to Alloy config

**Files:**
- Modify: `docker/alloy/config.alloy`

- [ ] **Step 1: Append the nginx components**

At the end of `docker/alloy/config.alloy`, add:

```alloy
// ---------- Host nginx file source ----------

// Discover access logs and tag them with vhost from filename
local.file_match "nginx_access" {
  path_targets = [
    {
      __path__ = "/var/log/nginx/*.access.log",
      service  = "nginx",
      log_type = "access",
    },
  ]
  sync_period = "30s"
}

local.file_match "nginx_error" {
  path_targets = [
    {
      __path__ = "/var/log/nginx/*.error.log",
      service  = "nginx",
      log_type = "error",
    },
  ]
  sync_period = "30s"
}

loki.source.file "nginx_access" {
  targets    = local.file_match.nginx_access.targets
  forward_to = [loki.process.nginx_access.receiver]
}

loki.source.file "nginx_error" {
  targets    = local.file_match.nginx_error.targets
  forward_to = [loki.process.nginx_error.receiver]
}

// Extract vhost from filename like /var/log/nginx/e-shuushuu.net.access.log
loki.process "nginx_access" {
  forward_to = [loki.write.default.receiver]

  stage.regex {
    source     = "filename"
    expression = "/var/log/nginx/(?P<vhost>[^/]+)\\.access\\.log"
  }

  // Combined log format
  stage.regex {
    expression = "^(?P<remote_addr>\\S+) - (?P<remote_user>\\S+) \\[(?P<time_local>[^\\]]+)\\] \"(?P<method>\\S+) (?P<path>\\S+) (?P<protocol>[^\"]+)\" (?P<status>\\d{3}) (?P<bytes_sent>\\d+) \"(?P<referer>[^\"]*)\" \"(?P<user_agent>[^\"]*)\""
  }

  stage.labels {
    values = {
      vhost   = "",
      service = "",
      status  = "",
      method  = "",
    }
  }
}

loki.process "nginx_error" {
  forward_to = [loki.write.default.receiver]

  stage.regex {
    source     = "filename"
    expression = "/var/log/nginx/(?P<vhost>[^/]+)\\.error\\.log"
  }

  stage.static_labels {
    values = {
      level = "error",
    }
  }

  stage.labels {
    values = {
      vhost   = "",
      service = "",
    }
  }
}
```

> **Cardinality check:** `status` (3 chars, ~30 distinct values), `method` (~10 distinct values), `vhost` (~1-5 distinct values) all qualify as low-cardinality labels. `path`, `remote_addr`, `user_agent`, `referer`, `bytes_sent` stay in the log line — do not promote to labels.

### Task 4.2: Mount the host nginx log directory into Alloy

**Files:**
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Add the volume mount**

In the `alloy:` service block, add to the `volumes:` list:

```yaml
      - /var/log/nginx:/var/log/nginx:ro
```

- [ ] **Step 2: Verify Alloy can read the files**

The Alloy container runs as uid 473 (the image's default `alloy` user) with `group_add: docker`. uid 473 is not a member of `adm` by default, so it cannot read `640 www-data adm`-mode nginx log files.

```bash
ls -la /var/log/nginx/e-shuushuu.net.access.log
# Default on Debian/Ubuntu: -rw-r----- 1 www-data adm
```

Two options:
- **Option A (preferred — minimal change):** add `adm` group to Alloy's `group_add`. Get gid: `getent group adm | cut -d: -f3` (commonly 4). Add `"4"` to `group_add` in compose.
- **Option B:** chmod the directory `o+rx` and files `o+r`. Less ideal — opens log read to every user on the host.

Choose A unless there is a reason to avoid it. Update `group_add`:

```yaml
    group_add:
      - "999"  # docker — confirm with `getent group docker`
      - "4"    # adm    — confirm with `getent group adm`
```

### Task 4.3: Bring up Alloy with new config, verify nginx ingestion

- [ ] **Step 1: Restart Alloy to pick up config and mount changes**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d alloy
docker logs --tail 50 shuushuu-alloy-prod
```

Expected: no permission errors on `/var/log/nginx/*`. If there are, fix `group_add` from Task 4.2 and re-run.

- [ ] **Step 2: Generate nginx traffic**

Hit any URL through the public edge (or `curl -H 'Host: e-shuushuu.net' http://127.0.0.1/`).

- [ ] **Step 3: Verify nginx access logs land**

In Grafana Explore:
```logql
{service="nginx", vhost="e-shuushuu.net"}
```
Expected: lines appear within 5s.

- [ ] **Step 4: Verify status label was extracted**

```logql
{service="nginx", status="200"}
```
Expected: returns recent 200 responses.

- [ ] **Step 5: Verify error log path**

Trigger an nginx error if possible (request a known-bad URL pattern), then:
```logql
{service="nginx", level="error"}
```
Expected: error log lines appear.

- [ ] **Step 6: Cardinality re-check**

```logql
count by (service, status, method, vhost) (count_over_time({service="nginx"}[5m]))
```
Expected: ≤ ~50 rows (handful of vhosts × ~30 status codes × ~10 methods, but most combinations don't actually occur).

### Task 4.4: Commit chunk 4

- [ ] **Step 1: Commit**

```bash
git add docker/alloy/config.alloy docker-compose.prod.yml
git commit -m "feat(logging): tail host nginx access/error logs in alloy"
```

---

## Chunk 5: Acceptance tests, retention test, ops runbook

Goal: run all 9 acceptance criteria from the spec, perform the concrete retention test, write the operator runbook. End state: feature is shippable.

### Task 5.1: Run spec acceptance criteria 1-7

For each criterion in the spec section "Acceptance criteria", run the test and record the result.

- [ ] **Step 1: AC#1 — Container logs flow end-to-end**

```bash
curl -s http://127.0.0.1:8000/api/v1/images/1111520 > /dev/null
sleep 5
# In Grafana Explore:
#   {service="api"} | line_format "{{.event}}"
# Expected: request_complete entry visible.
```

- [ ] **Step 2: AC#2 — JSON parsing**

In Grafana Explore:
```logql
{service="api"} | json | line_format "rid={{.request_id}} ms={{.elapsed_ms}}"
```
Expected: `request_id` and `elapsed_ms` extracted as fields, not raw strings.

- [ ] **Step 3: AC#3 — Host nginx ingestion** (already verified in Task 4.3)

Re-confirm:
```logql
{service="nginx", vhost="e-shuushuu.net"}
```

- [ ] **Step 4: AC#4 — Label cardinality bounded**

```logql
count by (service, level) (count_over_time({host="prod"}[5m]))
```
Expected: ≤ ~30 rows.

- [ ] **Step 5: AC#5 — Buffer survives Loki restart**

```bash
# In one terminal, generate continuous nginx traffic:
while true; do curl -s -o /dev/null https://e-shuushuu.net/; sleep 1; done &
TRAFFIC_PID=$!

# Restart Loki:
docker restart shuushuu-loki-prod

# Wait for Loki ready:
for i in {1..20}; do
  docker exec shuushuu-loki-prod wget -qO- http://localhost:3100/ready 2>/dev/null | grep -q ready && break
  sleep 3
done

# Stop traffic:
kill $TRAFFIC_PID
sleep 5
```

In Grafana, query the period spanning the restart:
```logql
sum by (vhost) (count_over_time({service="nginx"}[2m]))
```
Expected: no zero-count gap longer than ~30s. (Some short gap is acceptable for the Docker socket source per spec.)

- [ ] **Step 6: AC#6 — Logrotate doesn't break ingestion**

```bash
sudo logrotate -f /etc/logrotate.d/nginx
sleep 5
curl -s -o /dev/null https://e-shuushuu.net/
sleep 5
```

In Grafana:
```logql
{service="nginx", vhost="e-shuushuu.net"}
```
Expected: most recent line dated after the rotation timestamp.

- [ ] **Step 7: AC#7 — Compactor runs**

```bash
docker logs shuushuu-loki-prod 2>&1 | grep -i "compaction" | tail -5
```
Expected: at least one line containing `compaction completed` or similar.

### Task 5.2: AC#8 — Retention test (concrete procedure from spec)

**Files temporarily modified:**
- `docker/loki/loki-config.yaml` (reverted at end)

- [ ] **Step 1: Edit loki-config.yaml — shorten retention**

In `docker/loki/loki-config.yaml`, temporarily change:
```yaml
limits_config:
  retention_period: 1h     # was: 60d
  ...

compactor:
  compaction_interval: 1m  # was: 10m
  ...
```

- [ ] **Step 2: Restart Loki**

```bash
docker restart shuushuu-loki-prod
docker logs --tail 50 shuushuu-loki-prod | grep -i "retention\|compaction"
```

- [ ] **Step 3: Wait 90 minutes, then check**

```bash
# Initial chunk count (record this)
docker exec shuushuu-loki-prod sh -c 'find /loki/chunks -type f | wc -l'

# Wait 90m
sleep 5400

# Check again
docker exec shuushuu-loki-prod sh -c 'find /loki/chunks -type f | wc -l'
```

Expected: chunk count drops materially after retention deletion runs.

- [ ] **Step 4: Revert config and restart**

Edit `loki-config.yaml` back to `retention_period: 60d` and `compaction_interval: 10m`.

```bash
docker restart shuushuu-loki-prod
```

- [ ] **Step 5: Verify revert took effect**

```bash
docker exec shuushuu-loki-prod cat /etc/loki/loki-config.yaml | grep -E 'retention_period|compaction_interval'
```

- [ ] **Step 6: Do NOT commit the temporary edit**

Confirm `git diff docker/loki/loki-config.yaml` shows no changes. The file should be back to its committed state.

### Task 5.3: AC#9 — Disk pressure projection

This is a 7-day deferred check. Set a calendar reminder; no implementation today.

- [ ] **Step 1: Note baseline**

```bash
docker exec shuushuu-loki-prod sh -c 'du -sh /loki/chunks /loki/index'
```
Record the output. Re-run in 7 days, multiply by ~8.5 (60d/7d) for the projection. If projection ≥ 50GB, plan to drop noisy nginx paths or shorten retention.

(Consider scheduling a follow-up agent: "in 7 days, query Loki disk usage on prod and post the 60-day projection." See `/schedule` mention in end-of-turn summary.)

### Task 5.4: Write the operator runbook

**Files:**
- Create: `docs/log-operations.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Centralized Log Operations Runbook

How to query production logs from the centralized Loki + Grafana stack.

For the application-side logging API (structlog usage in code), see [logging-guide.md](logging-guide.md).
For the design rationale and architecture, see [plans/2026-04-25-centralized-logging-design.md](plans/2026-04-25-centralized-logging-design.md).

## Access

Grafana is bound to `127.0.0.1:3001` on the prod host — not exposed publicly. Reach it via SSH tunnel.

### One-time SSH config

Add to `~/.ssh/config` on your laptop:

```
Host shuu-prod-logs
  HostName <prod hostname>
  User <your user>
  LocalForward 3001 localhost:3001
  ServerAliveInterval 60
```

### Daily use

```bash
ssh shuu-prod-logs
# Leave terminal open; visit http://localhost:3001 in your browser.
# Login: admin / value of GRAFANA_ADMIN_PASSWORD in .env.prod on prod host.
```

## Sample LogQL queries

Use the **Explore** tab in Grafana, datasource `Loki`.

### Last hour of API errors

```logql
{service="api", level=~"error|critical"}
```

### Errors for a specific user

```logql
{service="api"} | json | user_id = "42" | level =~ "error|critical"
```

### Recent 5xx responses at the edge

```logql
{service="nginx"} | status =~ "5.."
```

### Slow API requests (>1s)

```logql
{service="api"} |= "request_complete" | json | elapsed_ms > 1000
```

### Trace a specific request across api + arq-worker

```logql
{service=~"api|arq-worker"} | json | request_id = "abc-123"
```

### Top 10 noisiest paths in nginx (last hour)

```logql
topk(10, sum by (path) (count_over_time({service="nginx"} | regexp `"\\S+ (?P<path>\\S+) HTTP` [1h])))
```

## Retention

60 days, deleted by Loki's compactor. To change:

1. Edit `retention_period` in `docker/loki/loki-config.yaml`
2. `docker restart shuushuu-loki-prod`
3. Commit and deploy the config change

## Disk usage

```bash
docker exec shuushuu-loki-prod du -sh /loki/chunks /loki/index
```

If approaching 50% of host free disk, shorten retention rather than letting Loki refuse writes.

## Restarting components

Each is independent. Restart in any order; Alloy buffers to disk during Loki outages.

```bash
docker restart shuushuu-loki-prod
docker restart shuushuu-alloy-prod
docker restart shuushuu-grafana-prod
```

## Adding a new ingestion source

To pull in another log file or another container, edit `docker/alloy/config.alloy`:

- New container: nothing to do — `discovery.docker` auto-discovers any new `shuushuu-*-prod` container.
- New file: add a `local.file_match` + `loki.source.file` + `loki.process` triple following the nginx pattern in the same file. Mount the file's directory into the alloy service in `docker-compose.prod.yml` if outside `/var/log/nginx`.

After config changes:
```bash
docker restart shuushuu-alloy-prod
docker logs --tail 50 shuushuu-alloy-prod  # check for parse errors
```
```

- [ ] **Step 2: Add cross-reference to logging-guide.md**

In `docs/logging-guide.md`, add this paragraph at the top of the file, right after the H1:

```markdown
> **For querying production logs**, see [log-operations.md](log-operations.md). This document covers the application-side structlog API (how to emit logs in Python code).
```

### Task 5.5: Commit chunk 5

- [ ] **Step 1: Commit the runbook and cross-reference**

```bash
git add docs/log-operations.md docs/logging-guide.md
git commit -m "docs(logging): add operator runbook for centralized log queries"
```

---

## Done

After Chunk 5, all 9 acceptance criteria pass and the runbook is in place. The system is shippable.

**Suggested follow-up (not in scope of this plan):**
- 7-day check-in on disk usage projection (AC#9 deferred portion).
- Decide whether to disable `access_log` for noisy static-asset paths in nginx config based on observed volume.
- If volume justifies it, add Grafana alert rules for 5xx rate or `level=critical` count (separate small plan).
