# Dockerfile for Shuushuu API with uv
FROM python:3.14-slim

# Install uv from Astral's uv image (copy binaries into a directory on PATH)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml ./

# Install Python dependencies using uv by installing the project (pyproject-based build)
RUN uv pip install --system -r pyproject.toml

# Copy only what's needed for runtime (explicit allowlist)
COPY app/ ./app/
COPY alembic/ ./alembic/

# Expose port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
