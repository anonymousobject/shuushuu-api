# Environment Loading: Single `.env` Per Host

## Current Architecture

Every environment (dev, test, prod) reads a single `.env` file from the host. The docker-compose base config loads this file for the api service; all overrides (dev/test/prod) inherit this behavior and add environment-specific settings on top.

### Why This Works

- **dev** and **test** run on dedicated hosts (dev workstation, test server), so a single `.env` per host is naturally unambiguous.
- **pytest** (isolated MariaDB) pins all database credentials explicitly in the Makefile, ignoring `.env` entirely.
- **prod** also has a dedicated host (separate private database tier), so a single `.env` is equally safe.

### Guards in Place

The Makefile enforces environment isolation via `check-env-*` targets that inspect `.env` contents before acting:
- `check-env-test`: asserts `DOMAIN=test.shuushuu.com`
- `check-env-prod`: asserts `NGINX_HOST=e-shuushuu.net`

These prevent accidental `make prod` on the dev box from silently consuming the wrong configuration.

## Simplification Checklist

### Repos (Already Complete)

- [x] **Frontend**: `.env` / `.env.local` / `.env.example` only; no dead `.env.development` or `.env.test`.
- [x] **API**: docker-compose base loads `.env`; no per-env file overrides.
- [x] **Both**: `.gitignore` correctly blocks `.env*` except `.env.example`.

### Hosts (Pending Coordinated Deployment)

#### Test Host (shuu): `~/shuushuu-api/.env.test` → `~/.env`

**Current state:**
```
.env                 # leftover dev config (July 6)
.env.test            # tracked test config (incomplete, documented)
.env.example         # repo's superset template
```

**Action:**
1. Merge `.env` + `.env.test` → `.env` (test values win on conflict)
   ```bash
   # On shuu:
   cd ~/shuushuu-api
   # Merge test values on top of dev base (preserves base keys unused by test)
   # Then remove the split:
   rm .env.test
   ```
2. Verify the merged `.env` has all keys from both files (test should fill in blanks from dev)
3. Restart the test stack:
   ```bash
   make test-down && make test-up
   ```
4. Smoke test: browse https://test.shuushuu.com, check container env:
   ```bash
   docker compose exec api env | grep -E 'ML_MODEL|DANBOORU|MEILISEARCH'
   ```
   Should output the merged values (test overrides where present, dev fallback otherwise).

#### Prod Host (kyouko): `docker-compose.prod.yml` → single `.env`

**Current state:**
```
.env                 # zero-byte fossil (May 8, no longer needed)
.env.prod            # real config, rendered by ansible
.env.test            # inherited from git, unused
.env.example         # repo's superset template
```

**Action:**
1. Ansible: update compose_project role to render `.env` instead of `.env.prod`
2. Delete the zero-byte `.env` stub and untrack `.env.test`:
   ```bash
   # On kyouko:
   rm .env               # fossil stub
   # (leave .env.prod until ansible re-renders)
   ```
3. Run ansible to render `.env` from vault:
   ```bash
   ansible-playbook playbooks/deploy.yml -t compose_project -i inventory
   ```
4. Delete the old `.env.prod` after verification:
   ```bash
   rm .env.prod
   ```
5. Restart prod stack:
   ```bash
   make prod-down && make prod-up
   ```
6. Verify compose config renders correctly:
   ```bash
   docker compose config | grep -A 5 'services:.*api:' | head -20
   ```

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Wrong host runs wrong env | Makefile guard checks DOMAIN/NGINX_HOST before `make test/prod` |
| `.env` lost to git clean | `.env*` in `.gitignore`; run `git status` to verify before checkout |
| Prod ansible doesn't render | Test ansible render in a safe branch first; roll back `.env.prod` if needed |
| Config keys missing after merge | Diff old + new against `.env.example`; `.env.example` is the maintained superset |

## Deployment Order

1. **Frontend repo**: no changes (already clean)
2. **API repo**: no changes (already clean)
3. **Test host** (shuu): merge `.env`, verify, restart
4. **Prod host** (kyouko): update ansible, render, verify, restart

All four steps can happen in parallel except ansible → restart on prod (render first, then restart).

## Rollback

Each host has the old split `.env.*` files in git history and local disk (`.env.prod` + `.env.test`).
- If merge/render goes wrong, restore the old files and revert ansible.
- The Makefile guards will catch any mismatched environment.
