# GitHub Actions Workflows

This directory contains CI/CD workflows for the Shuushuu API project.

## Workflows

### **CI** (`ci.yml`) - Comprehensive Pipeline ⭐

**The single, consolidated workflow for all CI/CD needs**

Runs on every push and pull request to `main` and `dev` branches.

**Jobs:**
1. **Lint & Format Check** - Code quality checks
   - Ruff linting (`ruff check`)
   - Ruff formatting (`ruff format --check`)
   - mypy type checking (continue-on-error: true)

2. **Tests** - Full test suite with MySQL database
   - Matrix strategy (Python 3.14)
   - MariaDB 12 service container
   - pytest with verbose output
   - Test results artifacts

3. **Security Scan** - Vulnerability scanning
   - pip-audit package check
   - Trivy filesystem scanner
   - SARIF upload to GitHub Security tab
   - CRITICAL and HIGH severity only

**Typical run time:** ~3-5 minutes

## Key Features

### Required Environment Variables

The test job sets all required environment variables:

```yaml
env:
  # Required application settings
  SECRET_KEY: test-secret-key-for-ci-only-not-for-production-use-min-32-chars
  DATABASE_URL: mysql+aiomysql://shuushuu:shuushuu_ci_password@127.0.0.1:3306/shuushuu_test?charset=utf8mb4
  DATABASE_URL_SYNC: mysql+pymysql://shuushuu:shuushuu_ci_password@127.0.0.1:3306/shuushuu_test?charset=utf8mb4
  # Test database URLs (used by conftest.py)
  TEST_DATABASE_URL: mysql+aiomysql://shuushuu:shuushuu_ci_password@127.0.0.1:3306/shuushuu_test?charset=utf8mb4
  TEST_DATABASE_URL_SYNC: mysql+pymysql://shuushuu:shuushuu_ci_password@127.0.0.1:3306/shuushuu_test?charset=utf8mb4
  # Optional settings with defaults
  ENVIRONMENT: development
  DEBUG: "True"
  REDIS_URL: redis://localhost:6379/0
```

### Database Service

Tests run against a real MariaDB database using GitHub Actions service containers:

```yaml
services:
  mysql:
    image: mariadb:12
    env:
      MYSQL_ROOT_PASSWORD: root_password
      MYSQL_DATABASE: shuushuu_test
      MYSQL_USER: shuushuu
      MYSQL_PASSWORD: shuushuu_ci_password
    ports:
      - 3306:3306
    options: >-
      --health-cmd="healthcheck.sh --connect --innodb_initialized"
      --health-interval=10s
      --health-timeout=5s
      --health-retries=5
```

### UV Package Manager

All workflows use `uv` for fast, reliable dependency management:

```yaml
- name: Install uv
  uses: astral-sh/setup-uv@v4
  with:
    enable-cache: true
    cache-dependency-glob: "uv.lock"

- name: Install dependencies
  run: uv sync --frozen --all-groups
```

Benefits:
- ⚡ Fast installation (~10-20 seconds)
- 🔒 Deterministic with `uv.lock`
- 💾 Caching enabled for faster subsequent runs

### Ruff Configuration

Ruff respects the `extend-exclude` configuration in `pyproject.toml`:
- Tests directory (`tests/`)
- Documentation (`docs/`)
- Scripts (`scripts/`)
- Root-level test files (`test_*.py`)

This ensures consistent linting behavior across:
- Local development (`uv run ruff check`)
- Pre-commit hooks
- CI/CD pipeline

### Matrix Strategy

The CI workflow supports testing against multiple Python versions:

```yaml
strategy:
  matrix:
    python-version: ['3.12']
    # Add more versions if needed:
    # python-version: ['3.11', '3.12', '3.13']
```

## Local Testing

### Run tests locally with the same environment

```bash
# Run tests with pytest
uv run pytest tests/ -v --tb=short --maxfail=5

# Run linting
uv run ruff check

# Run formatting check
uv run ruff format --check

# Run type checking
uv run mypy app/
```

### Test CI workflow locally with act

You can test the CI workflow locally using [act](https://github.com/nektos/act):

```bash
# Install act (macOS)
brew install act

# Run the entire CI workflow
act pull_request

# Run specific job
act -j lint
act -j test

# Use custom Docker image (for MySQL support)
act -j test -P ubuntu-latest=catthehacker/ubuntu:full-latest
```

## Workflow Status Badges

Add these badges to your `README.md`:

```markdown
![CI](https://github.com/YOUR_USERNAME/shuushuu-api/workflows/CI/badge.svg)
```

## Troubleshooting

### Tests fail with "Field required" errors

This means required environment variables are missing:
- `SECRET_KEY`
- `DATABASE_URL`
- `DATABASE_URL_SYNC`

**Solution:** Ensure the workflow file has all required env vars set in the "Run tests" step.

### Tests fail with database connection errors

1. Check MySQL service health in workflow logs
2. Verify `TEST_DATABASE_URL` environment variable is set correctly
3. Ensure health check is passing before tests run
4. Check the "Wait for MySQL to be ready" step

### Ruff checking excluded directories

If Ruff is checking `tests/`, `docs/`, or `scripts/`:
1. Verify `pyproject.toml` has `extend-exclude` configured
2. Ensure the workflow doesn't explicitly pass paths to ruff (just `ruff check`, not `ruff check app/ tests/`)
3. Clear the ruff cache: `uv run ruff clean`

### Slow workflow execution

1. Check if dependency cache is working (should see "Cache restored" in logs)
2. Consider using matrix strategy to parallelize tests
3. Review if all test dependencies are necessary

### Type checking failures (mypy)

Type checking is set to `continue-on-error: true` initially. Once your codebase is fully typed:

```yaml
- name: mypy - Type check
  run: uv run mypy app/
  # Remove this line to make type errors fail CI:
  # continue-on-error: true
```

### Security scan fails

The Trivy action version must match exactly:
- ✅ Correct: `aquasecurity/trivy-action@0.33.1`
- ❌ Wrong: `aquasecurity/trivy-action@v0.33.1`

## GitHub Repository Settings

### Branch Protection Rules

Recommended settings for `main` and `dev` branches:

1. Go to Settings → Branches → Add branch protection rule
2. Configure:
   - ✅ Require status checks to pass before merging
   - ✅ Require branches to be up to date before merging
   - Select status checks: `Lint & Format Check`, `Tests (Python 3.12)`, `Security Scan`
   - ✅ Require conversation resolution before merging
   - ✅ Do not allow bypassing the above settings

### Secrets (for future use)

For production deployments, add these secrets in repository settings:

- `CODECOV_TOKEN` - For code coverage reporting
- `DOCKER_USERNAME` / `DOCKER_PASSWORD` - For Docker registry
- Production database credentials (if needed)

## Architecture Decisions

### Why one consolidated workflow?

**Previously:** Had separate `ci.yml` and `test.yml` workflows
**Now:** Single `ci.yml` with multiple jobs

**Benefits:**
- ✅ No duplicate runs (saves GitHub Actions minutes)
- ✅ Single source of truth for CI configuration
- ✅ Easier to maintain and update
- ✅ Clear job dependencies and workflow
- ✅ Better resource utilization

If you need separate workflows in the future, consider:
- `ci.yml` - Fast checks on every PR (lint + quick tests)
- `extended-tests.yml` - Comprehensive tests on specific events
- `deploy.yml` - Deployment workflows

## Next Steps

1. ✅ **Branch protection enabled** - Require CI to pass before merging
2. **Add code coverage** - Track test coverage over time with pytest-cov
3. **Deployment workflow** - Auto-deploy to staging/production
4. **Release workflow** - Automate versioning and releases

## References

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [UV Documentation](https://docs.astral.sh/uv/)
- [Service Containers](https://docs.github.com/en/actions/using-containerized-services)
- [Ruff Configuration](https://docs.astral.sh/ruff/configuration/)
- [Trivy Action](https://github.com/aquasecurity/trivy-action)
