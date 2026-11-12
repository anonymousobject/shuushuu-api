# Dockerfile for Shuushuu API with uv
FROM python:3.14-slim

# Install uv from Astral's uv image. Pinned because :latest defeats the
# point of locking the Python deps below — a uv minor release can change
# `sync` semantics or lockfile format. Bump deliberately, same as `uv lock`.
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# The base image ships pip, which nothing here uses: uv installs the
# dependencies and runs the app. pip vendors its own copies of urllib3, msgpack
# and setuptools, which lag behind their fixes and can't be bumped through
# uv.lock, so the image scan (.github/workflows/image-build.yml) keeps flagging
# them.
RUN pip uninstall -y pip

# Install system dependencies. The upgrade picks up Debian security fixes
# published since the base image was last rebuilt.
RUN apt-get update && apt-get upgrade -y && apt-get install -y \
    gcc \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy lock + manifest first so the dep layer caches independently of source
# changes. Using uv.lock (via `--frozen`) is what keeps rebuilds reproducible:
# a stray transitive bump (e.g. a driver minor bump) can't sneak in without
# an explicit `uv lock` regenerate, which would also fail CI if it broke.
COPY pyproject.toml uv.lock ./

# Install runtime deps only (no dev group). --no-install-project because
# the app/ source isn't copied yet — installed in the layer below.
RUN uv sync --frozen --no-install-project --no-dev

# Bake the lockfile hash so the worker can detect a stale image at startup.
# docker-compose mounts ./uv.lock from the host at runtime; if the hash
# differs, the image was built against an older lockfile and must be rebuilt.
RUN sha256sum uv.lock | awk '{print $1}' > /app/.uv-lock-hash

# Copy only what's needed for runtime (explicit allowlist)
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/

# Install the project itself (cheap; deps are already in the venv).
RUN uv sync --frozen --no-dev

# Expose port
EXPOSE 8000

# --no-sync: deps are already in sync from the build steps above; don't let
# `uv run` re-check on every container start.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
