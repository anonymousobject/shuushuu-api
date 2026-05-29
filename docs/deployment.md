# Production deployment

Production runs `shuushuu-api` and `shuushuu-frontend` (sibling repos) as a
single Docker Compose stack fronted by an nginx TLS edge. App code is deployed
with **zero downtime** via the [`docker-rollout`](https://github.com/wowu/docker-rollout)
CLI plugin.

## How zero-downtime works here

nginx proxies `/api/` to `api:8000` and `/` to `frontend:3000` using the
*variable* form of `proxy_pass` plus `resolver 127.0.0.1:11 valid=10s`
(`docker/nginx/nginx.conf`). That means nginx re-resolves those service names
against Docker's embedded DNS every ≤10s, at request time, instead of pinning
an IP at config load.

`docker rollout api` exploits this:

1. Scales `api` to two replicas — a new container (new image) starts alongside
   the running one. Neither publishes a host port, so they coexist.
2. Waits for the new replica's healthcheck (`GET /health`) to pass.
3. Removes the old replica. nginx's next DNS refresh drops it from rotation.

No requests are dropped, and nginx is never restarted. The same applies to
`frontend` (it already has a healthcheck).

This required (see `docker-compose.prod.yml`):

- `api` / `frontend` have **no `container_name`** (can't fix a name across two
  replicas) and **no host port publish** (two replicas can't bind one port).
  Reach them on the host via `docker compose exec`, or through nginx.
- `api` has a compose `healthcheck` against `/health`.
- Alloy derives its `service` log label from the compose service label rather
  than the container name (`docker/alloy/config.alloy`), so logs survive the
  rename and the transient second replica.

## One-time host setup

Install the plugin on the production host:

```bash
mkdir -p ~/.docker/cli-plugins
curl -fsSL https://raw.githubusercontent.com/wowu/docker-rollout/main/docker-rollout \
  -o ~/.docker/cli-plugins/docker-rollout
chmod +x ~/.docker/cli-plugins/docker-rollout
docker rollout --help   # verify
```

## Deploying app changes

```bash
# 1. Pull latest on the changed repo(s)
git -C ~/shuushuu-api pull        # and/or ~/shuushuu-frontend

# 2. If the release includes a DB migration, apply it FIRST (see below)
make prod-migrate

# 3. Zero-downtime rollout (default rolls api + frontend)
make prod-deploy                  # or: make prod-deploy api  /  make prod-deploy frontend
```

## Migrations are a gated step

`prod-deploy` does **not** run migrations. Run `make prod-migrate` explicitly
when a release contains one, *before* `prod-deploy`.

Because a rollout briefly runs the old and new app versions against the **same
database at once**, migrations must be **backward-compatible** for the duration
of the overlap (expand/contract):

- **Safe to ship with a rollout:** additive changes — new nullable columns, new
  tables, new indexes. The still-running old container ignores them.
- **NOT safe in the same release:** dropping or renaming a column/table the old
  code still reads, or tightening a constraint the old code can still violate.
  Split these: deploy code that no longer uses the column first, then drop it in
  a *later* release once no old container remains.

## nginx / infra changes (cause brief downtime)

`docker-rollout` is only for stateless app services behind nginx. For nginx
config, TLS, mediawiki, or other infra services, use:

```bash
make prod-restart nginx           # force-recreate a specific service
```

`make prod-restart` with **no arguments** force-recreates *everything*,
including nginx — that drops the TLS edge for its boot and is the source of the
old 30–60s outage. Scope it to the service you changed.
