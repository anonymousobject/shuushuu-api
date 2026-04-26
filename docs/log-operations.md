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
