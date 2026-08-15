# Centralized Logging with Loki + Grafana + Alloy

## Goal

Provide persistent, queryable logs for all production services on a single host. Replace the current "ssh in and run `docker logs` per container" workflow with a single web UI that supports structured queries across containers and host nginx logs, with 60-day retention.

## Context

- Production host runs a mix: API, frontend (SvelteKit), arq-worker, redis, iqdb in Docker; MariaDB, nginx, PHP-FPM (legacy `shuu-php`) on the host.
- API and arq-worker already emit structured JSON via structlog (`app/core/logging.py`) to stdout, including `request_id`, `user_id`, and `level`. No app code changes required.
- Production compose (`docker-compose.prod.yml`) currently uses Docker's `json-file` driver with rotation (50MB × 5 files). Logs are container-local, vanish on container removal, and have no aggregation.
- Host nginx logs to `/var/log/nginx/e-shuushuu.net.{access,error}.log` using the standard `combined` log format. The same vhost serves the API, frontend, wiki, and forums.
- A separate `2026-03-05-goaccess-analytics-design.md` covers visitor analytics off the same nginx logs. That is read-only analysis on the host. This design is complementary: it ingests the same files into a queryable store for operational debugging.
- Single host. ~700GB free disk. No multi-region or multi-host log shipping needed.
- Solo operator initially. Web UI accessed via SSH tunnel; not exposed to the public internet.

## Non-goals

Explicitly out of scope to keep this minimal:

- Alerting (no Alertmanager, no PagerDuty integration). Layerable later via Grafana alert rules.
- Metrics (no Prometheus, no Mimir).
- Tracing (no Tempo, no OpenTelemetry traces).
- Multi-host log shipping.
- Log encryption at rest beyond whatever the host disk provides.
- Ingesting MariaDB slow log, PHP-FPM logs, journald, or sshd logs. Same Alloy pattern can be extended later.
- Public Grafana exposure with multi-user auth.
- Backups of the Loki data volume. If the host has any generic backup job, the implementation should explicitly exclude `loki_data`, `alloy_data`, and `grafana_data` from it — the chunks volume can grow into the tens of GB and is operational telemetry, not data of record.
- Canned dashboards. Grafana Explore + LogQL only at first; build dashboards once query patterns are known.

## Architecture

Three new Docker services in `docker-compose.prod.yml`, all bound to localhost only:

```
                         host
  ┌────────────────────────────────────────────────────┐
  │   ┌──────────────┐                                 │
  │   │ nginx (host) │── /var/log/nginx/*.log ──┐      │
  │   └──────────────┘                          │      │
  │                                             ▼      │
  │   Docker network                       ┌──────────┐│
  │   ┌─────────────┐ stdout (JSON)        │  alloy   ││
  │   │ api         │──┐                   │ (agent)  ││
  │   ├─────────────┤  │  via Docker       └────┬─────┘│
  │   │ frontend    │──┤  socket discovery      │      │
  │   ├─────────────┤  │                        │ push │
  │   │ arq-worker  │──┤                        ▼      │
  │   ├─────────────┤  │                   ┌──────────┐│
  │   │ redis, iqdb │──┘                   │   loki   ││
  │   └─────────────┘                      │ (storage)││
  │                                        └────┬─────┘│
  │                                             │      │
  │                                             ▼      │
  │   127.0.0.1:3001 ◄───────────────── ┌──────────┐  │
  │                                     │ grafana  │  │
  │                                     │  (UI)    │  │
  │                                     └──────────┘  │
  └────────────────────────────────────────────────────┘
                          ▲
                          │ ssh -L 3001:localhost:3001
                       laptop
```

| Service | Role | Resources (rough) | Port |
|---|---|---|---|
| `alloy` | Log shipper. Reads Docker socket, tails host nginx files, parses JSON, applies labels, ships to Loki. Buffers to disk. | 100–300 MB RAM | none external |
| `loki` | Single-binary store and query engine. Filesystem chunks, TSDB index. | 200–500 MB RAM | none external |
| `grafana` | Read-only UI for LogQL queries. | 100–200 MB RAM | `127.0.0.1:3001` |

Volumes: `loki_data` (chunks + index, the bulk), `alloy_data` (WAL buffer), `grafana_data` (datasource/dashboard config).

## Components

### 1. Grafana Alloy (log shipper)

One agent on the host, in a Docker container, with two responsibilities.

**Container log discovery (Docker socket):**

Alloy mounts `/var/run/docker.sock` read-only and uses `discovery.docker` to enumerate running containers. New containers appear automatically — no config edit when a service is added.

Each container's stdout/stderr stream becomes a Loki stream with these labels:

| Label | Source | Example values | Why a label |
|---|---|---|---|
| `service` | container name minus `shuushuu-` prefix and `-prod` suffix | `api`, `frontend`, `arq-worker`, `redis`, `iqdb` | Coarse routing, low cardinality |
| `host` | hostname | `prod` | Future-proof for staging |
| `level` | extracted from JSON `level` field, fallback `info` | `debug`, `info`, `warning`, `error`, `critical` | Common filter, low cardinality |
| `compose_project` | Docker label `com.docker.compose.project` | `shuushuu-api` | Distinguishes if multiple projects share a host |

High-cardinality fields (`request_id`, `user_id`, `image_id`, `path`, `remote_addr`) stay in the log line. They are queryable via LogQL filters but not indexed as labels. This is a Loki performance requirement: each unique combination of label values creates a separate stream, and putting `user_id` on a label would create a stream per user.

**Structured JSON parsing:**

`loki.process` runs a `stage.json` that:

1. Detects JSON-shaped lines from api and arq-worker (structlog output).
2. Promotes the `level` field to a label.
3. Leaves all other fields in the log line for downstream `| json` parsing in LogQL.

Non-JSON lines (uvicorn startup banner, redis pseudo-format, iqdb plain text) pass through with `level=info` fallback.

**Per-service quirks:**

- **redis** — emits `16:M 25 Apr 2026 14:30:01.123 # message` style. Treated as plain text with `level=info` fallback. The `# / * / . / -` severity markers stay in the body and are searchable via LogQL `|~` regex if needed. Considered parsing them into the `level` label, but the volume on this host is too low to justify the maintenance.
- **iqdb** — plain text. No parsing; `level=info` fallback.
- **frontend** (SvelteKit) — log format depends on what the frontend emits; locked down during implementation. If it ships JSON, parse it; otherwise treat like iqdb.
- **dev-only nginx container** (in `docker-compose.yml`, not `docker-compose.prod.yml`) — same path as the host nginx (Section 2), but ingested via the Docker socket discovery, not file tail. Out of scope for the prod design but the agent config should work in dev too without modification.

**Drops at the agent:**

- Internal healthcheck noise (`/health` 200s) is dropped at Alloy via a filter on `path`. Configurable per-service.
- Nothing else dropped initially. Tune down once volume is observed in production.

**Agent configuration files:** `docker/alloy/config.alloy` (Alloy's flow-based config language).

### 2. Host nginx file ingestion

Nginx writes to:

- `/var/log/nginx/e-shuushuu.net.access.log`
- `/var/log/nginx/e-shuushuu.net.error.log`

Alloy mounts `/var/log/nginx:/var/log/nginx:ro` and uses `local.file_match` with patterns `*.access.log` and `*.error.log` so any future vhost is auto-included. The vhost name (e.g. `e-shuushuu.net`) is extracted from the filename and applied as a `vhost` label.

**Log rotation:** the host's logrotate config already rotates these files. Alloy follows rotation natively via inode tracking; no extra config required.

**Access log parsing** (`combined` format):

```
1.2.3.4 - - [25/Apr/2026:14:30:01 +0000] "GET /api/v1/images/123 HTTP/1.1" 200 1234 "https://e-shuushuu.net/" "Mozilla/5.0..."
```

A `stage.regex` extracts fields. Indexing decisions:

| Field | Indexed as label? | Rationale |
|---|---|---|
| `service=nginx` | yes | Coarse routing |
| `host=prod` | yes | Future-proof |
| `vhost` | yes | Bounded set; useful for "errors on which site?" |
| `status` | yes | 3-digit string. ~30 distinct values, queryable as range (`status >= 400`) |
| `method` | yes | Bounded set |
| `remote_addr`, `path`, `user_agent`, `referer`, `bytes_sent`, `request_time` | no | High cardinality, kept in line, queryable via filter expressions |

**Error log parsing:** error logs use the format `2026/04/25 14:30:01 [error] 12345#0: ...`. Apply `service=nginx`, `level=error`, `vhost=<from filename>`, pass through as text. Errors are infrequent enough that finer parsing is not worth the maintenance.

**Volume warning:** the access log will be the largest single source. Plan to:

1. Run with full ingestion at the agreed retention initially.
2. After one week of real volume data, decide whether to disable `access_log` for noisy paths in nginx config (image thumbs, static assets). This is done in nginx, not Alloy — Alloy ingests whatever nginx writes.

### 3. Loki storage and retention

**Mode:** single-binary (monolithic). One process handles ingestion, query, compaction, and retention. Officially supported up to ~100GB/day ingest; this site will be at well under 1% of that ceiling.

**Storage backend:** local filesystem via the `loki_data` Docker volume.

```
loki_data layout:
├── chunks/      # compressed log chunks (the bulk; tens of GB at 60d)
├── index/       # TSDB index (small, MBs)
├── wal/         # write-ahead log (in-flight writes, hundreds of MB)
└── compactor/   # compaction state
```

**Schema:** a single TSDB period pinned from the start so future migrations are not needed:

```yaml
schema_config:
  configs:
    - from: 2026-04-25
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

Chunks compressed with snappy.

**Retention:** 60 days. Compactor runs in-process every 10 minutes for chunk compaction; retention deletion runs once a day.

```yaml
limits_config:
  retention_period: 60d

compactor:
  retention_enabled: true
  delete_request_store: filesystem
  compaction_interval: 10m
```

**Per-stream limits:**

| Limit | Value | Reason |
|---|---|---|
| `ingestion_rate_mb` | 16 | Burst protection against traffic spikes |
| `ingestion_burst_size_mb` | 32 | |
| `max_streams_per_user` | 5000 | Far above expected with our label scheme; sanity cap against label-cardinality regressions. With `auth_enabled: false`, Loki uses a single tenant `fake`, so this is effectively a global cap. |
| `max_query_length` | 30d | Default unlimited; cap prevents accidentally querying all 60d in Explore |
| `max_query_lookback` | 60d | Matches retention |

**Auth:** single-tenant, `auth_enabled: false`. Loki is reachable only on the Docker network; only Alloy and Grafana are clients.

**Disk pressure:** Loki retention is time-based, not size-based. At 700GB free this is a non-issue for years. Operational guidance: if `loki_data` ever exceeds 50% of free disk, shorten retention rather than letting writes fail.

**Loki configuration file:** `docker/loki/loki-config.yaml`.

### 4. Grafana

```yaml
grafana:
  image: grafana/grafana:11.4.0  # pin minor, bump deliberately
  container_name: shuushuu-grafana-prod
  ports: !override
    - "127.0.0.1:3001:3000"
  environment:
    - GF_SECURITY_ADMIN_USER=admin
    - GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana_admin_password
    - GF_AUTH_ANONYMOUS_ENABLED=false
    - GF_USERS_ALLOW_SIGN_UP=false
  volumes:
    - grafana_data:/var/lib/grafana
    - ./docker/grafana/provisioning:/etc/grafana/provisioning:ro
  secrets:
    - grafana_admin_password
  logging: *default-logging
  restart: unless-stopped
```

Port `3001` rather than `3000` because the frontend container already binds `127.0.0.1:3000` in prod.

**Auth:** single admin user, password from a Docker secret (host file `docker/grafana/grafana_admin_password.txt`, gitignored, populated manually). Anonymous and signup disabled. With SSH-tunnel-only access, the admin login is defense-in-depth.

**Datasource provisioning** (`docker/grafana/provisioning/datasources/loki.yml`):

```yaml
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
```

`editable: false` enforces config-as-code: edit the YAML and redeploy, not the UI.

**Dashboards:** none initially. Grafana Explore is sufficient for ad-hoc LogQL. Build dashboards once recurring query patterns emerge, provisioned via the same `/etc/grafana/provisioning/dashboards/` mechanism.

### 5. Access pattern

SSH tunnel from the operator's laptop. Suggested `~/.ssh/config` snippet documented in `docs/LOGGING.md`:

```
Host shuu-prod-logs
  HostName <prod hostname>
  User <user>
  LocalForward 3001 127.0.0.1:3001
  ServerAliveInterval 60
```

Operator workflow: `ssh shuu-prod-logs`, then open `http://localhost:3001` in a browser.

## Data flow

1. Container writes to stdout (JSON for api/arq, plain or pseudo-structured for others).
2. Docker daemon captures stdout to its log driver (still `json-file` so `docker logs` keeps working).
3. Alloy reads from the Docker socket via `discovery.docker` + `loki.source.docker`, applies labels, parses JSON, ships to Loki.
4. Independently, Alloy tails `/var/log/nginx/*.{access,error}.log`, applies regex parsing, ships to Loki.
5. Loki accepts the push, writes to WAL, then to chunks. Compactor runs periodically. Retention deletion runs daily.
6. Grafana queries Loki on demand via LogQL when the operator opens Explore.

## Error handling and resilience

| Failure | Behavior |
|---|---|
| Loki down briefly | Alloy buffers to its WAL on `alloy_data` volume. Catches up when Loki returns. |
| Loki down for hours | WAL fills up; Alloy starts dropping oldest buffered logs. Container `docker logs` still works because Docker's `json-file` driver is unchanged. Host nginx log files are untouched. |
| Alloy down | New container logs accumulate in Docker's `json-file` (rotated at 50MB × 5). Host nginx files keep growing. On Alloy restart, it resumes from last offset for files; for Docker socket it picks up "now" — there will be a gap covering the outage window. |
| `loki_data` volume full | Loki refuses writes; Alloy buffers; eventually drops. Detect via existing host disk monitoring. |
| Logrotate runs while Alloy is reading | Alloy follows by inode, not filename. Continues with the new file automatically. |
| Container crash loop generating high log volume | Per-stream `ingestion_rate_mb` limit kicks in; excess is rejected at Loki. Alloy retries; eventually drops. The crash is visible in logs already collected before the limit. |
| JSON parse failure on a structlog line | Line is shipped without the JSON-extracted fields; raw text in body. `level` falls back to `info`. Investigation possible via raw text query. |

## Security considerations

- **Docker socket mount in Alloy** — Alloy mounts `/var/run/docker.sock` read-only. The "read-only" mount is on the file, not the API surface; a compromised Alloy process could still call the Docker API and exec into containers. Mitigation: pin Alloy to a specific minor version, watch advisories. Same risk pattern as cAdvisor, Portainer, Watchtower. Accepted.
- **Log content can be sensitive** — IPs, user IDs, path of every request, error stack traces. Logs never leave the host. Grafana is localhost-only. SSH tunnel for access.
- **Grafana admin password** — Docker secret from a gitignored file; not in env vars or compose YAML.
- **No write access to Loki from Grafana** — Loki is single-tenant with no auth, and Grafana has no Loki write API to call. Grafana is read-only by design.
- **Public exposure path is not opened** — design explicitly stays SSH-tunnel-only. Adding option (b) (nginx + basic auth) later would be additive: nginx vhost, basic auth, no Loki/Grafana config changes.

## Acceptance criteria

After deploying to prod, the following are verified manually:

1. **Container logs flow end-to-end.** Hit `curl http://localhost:8000/api/v1/images/1111520`. Within ~5s, `{service="api"}` in Grafana Explore returns the `request_complete` log entry.
2. **Structured JSON parsing works.** Same query with `| json` extracts `request_id`, `elapsed_ms`, `user_id` as fields.
3. **Host nginx ingestion works.** Hit any URL through the public edge. `{service="nginx", vhost="e-shuushuu.net"}` shows the access log line within ~5s.
4. **Label cardinality is bounded.** `count by (service, level) (count_over_time({host="prod"}[5m]))` returns ≤ ~30 streams. If it returns hundreds, label scheme has a regression.
5. **Buffer survives Loki restart.** `docker restart shuushuu-loki-prod` while traffic flows; verify no gap in nginx access logs after Loki returns. Brief gap acceptable for Docker socket source (covered in error handling).
6. **Logrotate does not break ingestion.** `sudo logrotate -f /etc/logrotate.d/nginx`, verify new lines continue to appear in queries.
7. **Compactor runs.** Loki container logs show `level=info msg="compaction completed"` within first 24h.
8. **Retention is enforced.** Concrete manual test: temporarily set `retention_period: 1h` and `compaction_interval: 1m` in `loki-config.yaml`, restart Loki, confirm chunks older than 1h are deleted within 24h via `du` or chunk file listing, then revert to 60d. Run this once during implementation; do not wait 60 days for natural validation.
9. **Disk pressure tracked.** After 7 days, `du -sh /var/lib/docker/volumes/loki_data_prod/_data` extrapolates to a 60-day projection of < 50GB. If projecting ≥ 50GB, decide whether to drop noisy nginx paths in nginx config or shorten retention.

## Open questions / deferred decisions

- Frontend log format. Determined during implementation as the **first implementation step** — `docker logs shuushuu-frontend-prod` to inspect, then write the matching Alloy parser before the rest of the agent config is finalized.
- Exact Alloy and Loki version pins. Pick latest stable at implementation time; pin to minor version.
- Whether to suppress `access_log` for static asset paths in nginx config. Decide after measuring real volume.

## Future extensions (not in this design)

- Add MariaDB slow log, PHP-FPM logs, journald via additional Alloy file/journal sources.
- Add Grafana alert rules on LogQL queries (e.g., 5xx rate > N/min).
- Add Prometheus + Mimir for metrics, Tempo for traces — the same Grafana already in place would query all three.
- Public Grafana exposure via the existing host nginx and `.htpasswd` infrastructure if multi-user / mobile access becomes useful.
- Off-host backup of `loki_data` if log history is ever deemed data-of-record.
