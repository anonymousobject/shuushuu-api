# Centralized Log Operations Runbook

How to query production logs from the centralized Loki + Grafana stack.

For the application-side logging API (structlog usage in code), see [logging-guide.md](logging-guide.md).
For the design rationale and architecture, see [plans/2026-04-25-centralized-logging-design.md](plans/2026-04-25-centralized-logging-design.md).

## First-time setup on the prod host

Before bringing the stack up for the first time, generate and store the Grafana admin password:

```bash
# On the prod host, in the shuushuu-api repo directory:
echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)" >> .env.prod
```

`.env.prod` is gitignored. Note the password — you'll need it to log in to Grafana.

Verify the docker and adm group GIDs match the values in `docker-compose.prod.yml`:

```bash
getent group docker | cut -d: -f3   # expected: 999 on Debian/Ubuntu
getent group adm    | cut -d: -f3   # expected: 4 on Debian/Ubuntu
```

If either differs, update the `group_add` list in the alloy service in `docker-compose.prod.yml` BEFORE bringing the stack up. The wrong gid means Alloy stays healthy but ingests zero logs from the affected source — silent failure.

### Bringing the stack up

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d loki grafana alloy
```

Verify all three reach a healthy state (loki and grafana have healthchecks; alloy does not — see "Operational notes" below):

```bash
docker ps --filter name=shuushuu-loki-prod --filter name=shuushuu-grafana-prod --filter name=shuushuu-alloy-prod
```

Then confirm Alloy is actually shipping logs by querying Loki via Grafana:

```bash
curl -sG -u "admin:${GRAFANA_ADMIN_PASSWORD}" \
  "http://127.0.0.1:3001/api/datasources/proxy/uid/loki/loki/api/v1/query_range" \
  --data-urlencode 'query={host="prod"}' \
  --data-urlencode "start=$(date -u -d '5 minutes ago' +%s%N)" \
  --data-urlencode 'limit=5' | jq '.data.result | length'
```

A number ≥ 1 means logs are flowing. Zero means Alloy isn't ingesting — check `docker logs shuushuu-alloy-prod` for parse errors and re-verify the `group_add` GIDs.

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

## Operational notes

**Backups.** The `loki_data_prod`, `alloy_data_prod`, and `grafana_data_prod` volumes are explicitly **not** backed up — logs are operational telemetry, not data of record. If the host runs a generic backup pipeline (rsync, restic, etc.), exclude these three volumes; the `loki_data_prod` chunks volume can grow into the tens of GB.

**Alloy has no healthcheck.** The `grafana/alloy` image ships without `wget`/`curl`/`nc`, so the standard HTTP probe pattern doesn't work. Alloy is a leaf service (nothing depends on its health), and `restart: unless-stopped` plus Docker's process supervision are sufficient. To verify Alloy is doing its job, query Loki for any recent `{host="prod"}` lines (see "Bringing the stack up" above).

**Recovering from a stuck Alloy WAL.** If Alloy's WAL gets corrupted (e.g., disk full mid-write) and Alloy refuses to start, the safest recovery is to wipe the volume and restart:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod stop alloy
docker volume rm alloy_data_prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.prod up -d alloy
```

Cost: a small gap in container log history covering the outage. Host nginx files are unaffected (Alloy resumes from inode + offset).

**Recovering from a corrupted Loki chunks volume.** Same pattern: stop Loki, `docker volume rm loki_data_prod`, restart. Cost: full loss of log history. Mitigation: this should be rare; the common failure mode is "disk full" which is preventable by monitoring disk usage (see "Disk usage" above).

## Adding a new ingestion source

To pull in another log file or another container, edit `docker/alloy/config.alloy`:

- New container: nothing to do — `discovery.docker` auto-discovers any new `shuushuu-*-prod` container.
- New file: add a `local.file_match` + `loki.source.file` + `loki.process` triple following the nginx pattern in the same file. Mount the file's directory into the alloy service in `docker-compose.prod.yml` if outside `/var/log/nginx`.

After config changes:

```bash
docker restart shuushuu-alloy-prod
docker logs --tail 50 shuushuu-alloy-prod  # check for parse errors
```
