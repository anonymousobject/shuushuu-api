# Quick Start Guide - MySQL + uv Version

## What Changed Based on Your Feedback ✅

1. **Using uv** instead of Poetry - Much faster, simpler
2. **Staying with MySQL** - No database migration needed, save 6+ weeks
3. **BackgroundTasks** instead of Celery - Simple, sufficient for your needs
4. **Focus on backend/frontend** - Not database complexity

## Benefits of These Choices

✅ **Faster setup**: 5 minutes instead of 30 minutes  
✅ **Familiar database**: Use what you know (MySQL)  
✅ **Simpler tasks**: No Celery/Beanstalk complexity  
✅ **Shorter timeline**: 22-28 weeks instead of 28-40 weeks  
✅ **Same power**: Still get all FastAPI benefits  

---

## Setup in 10 Steps (15 minutes)

### 1. Install uv
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Add to PATH (it will tell you how)
source $HOME/.cargo/env  # Linux/Mac
```

### 2. Create Project
```bash
mkdir shuushuu-api && cd shuushuu-api
```

### 3. Copy Files
Copy these files from the outputs directory:
- `pyproject.toml` → project root
- `docker-compose-mysql.yml` → `docker-compose.yml`
- `Dockerfile` → project root
- `app_config_mysql.py` → `app/config.py`
- `example_image_upload.py` → reference (shows how to use BackgroundTasks)

### 4. Create Project Structure
```bash
mkdir -p app/{api/v1,core,models,schemas,services,tasks,utils}
mkdir -p tests/{api,models,services}
mkdir -p alembic/versions
mkdir -p storage/{fullsize,thumbs}
```

### 5. Initialize uv
```bash
# This creates .venv and installs dependencies
uv sync

# Or manually add dependencies (same as in pyproject.toml)
uv add fastapi[standard] sqlalchemy[asyncio] aiomysql alembic redis arq \
  pillow python-multipart python-jose[cryptography] passlib[bcrypt] \
  pydantic-settings structlog httpx

uv add --dev pytest pytest-asyncio pytest-cov black ruff mypy
```

### 6. Create .env File
```bash
cat > .env << 'EOF'
# Environment
ENVIRONMENT=development
DEBUG=True

# Database (MySQL)
DATABASE_URL=mysql+aiomysql://shuushuu:shuushuu_dev_password@localhost:3306/shuushuu?charset=utf8mb4
DATABASE_URL_SYNC=mysql+pymysql://shuushuu:shuushuu_dev_password@localhost:3306/shuushuu?charset=utf8mb4

# Redis
REDIS_URL=redis://localhost:6379/0

# Security (CHANGE THIS!)
SECRET_KEY=your-secret-key-here-must-be-at-least-32-characters-long

# Task Queue
TASK_QUEUE_TYPE=background

# IQDB
IQDB_HOST=localhost
IQDB_PORT=5588

# CORS
CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# Storage
STORAGE_PATH=/shuushuu/images
EOF
```

### 7. Start Docker Services
```bash
docker-compose up -d

# Wait for MySQL to be ready (about 30 seconds)
docker-compose logs -f mysql
# Wait for: "ready for connections"
```

### 8. Set Up Database
```bash
# Initialize Alembic
uv run alembic init alembic

# Edit alembic/env.py (see instructions below)

# Create first migration
uv run alembic revision --autogenerate -m "Initial schema"

# Apply migration
uv run alembic upgrade head
```

### 9. Run the API
```bash
uv run uvicorn app.main:app --reload

# You should see:
# INFO:     Uvicorn running on http://127.0.0.1:8000
# INFO:     Application startup complete
```

### 10. Test It!
```bash
# Health check
curl http://localhost:8000/health

# View auto-generated docs
open http://localhost:8000/docs

# View database in Adminer
open http://localhost:8080
# Server: mysql, User: shuushuu, Password: shuushuu_dev_password
```

---

## Alembic Setup (Step 8 Detail)

Edit `alembic/env.py` to use async SQLAlchemy with MySQL:

```python
# alembic/env.py
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import asyncio

# Import your models and config
from app.core.database import Base
from app.config import settings
import app.models  # This imports all models

config = context.config

# Override with your DATABASE_URL_SYNC (for migrations)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

## Using Your Existing MySQL Database

If you want to use your existing Shuushuu database:

```bash
# 1. Update .env to point to existing database
DATABASE_URL=mysql+aiomysql://user:pass@host:port/existing_shuushuu_db?charset=utf8mb4

# 2. Generate manual migration from existing schema
# for autogenerate to work, SQLAlchemy models will need to be defined
uv run alembic revision -m "Initial schema from existing database"


# 3. Review the migration file in alembic/versions/
# Make sure it matches your existing schema

# 4. Mark as applied without running (since tables already exist)
uv run alembic stamp head

# Now your code will work with the existing database!
```

---

## Common uv Commands

```bash
# Run any command
uv run <command>

# Examples:
uv run uvicorn app.main:app --reload
uv run pytest
uv run alembic upgrade head
uv run black app/
uv run ruff check app/

# Add dependency
uv add package-name

# Add dev dependency
uv add --dev package-name

# Remove dependency
uv remove package-name

# Update dependencies
uv sync --upgrade

# Show installed packages
uv pip list

# Activate venv (optional - uv run does this automatically)
source .venv/bin/activate
```

---

## Development Workflow

### Day-to-Day Development
```bash
# 1. Start Docker services (once)
docker-compose up -d

# 2. Run API with auto-reload
uv run uvicorn app.main:app --reload

# 3. Make changes to code
# API automatically reloads on file save

# 4. Run tests
uv run pytest

# 5. Format code
uv run black app/
uv run ruff check --fix app/
```

### Creating New Endpoints
```bash
# 1. Create new model in app/models/
vim app/models/new_feature.py

# 2. Create Pydantic schemas in app/schemas/
vim app/schemas/new_feature.py

# 3. Create migration
uv run alembic revision --autogenerate -m "Add new_feature table"

# 4. Apply migration
uv run alembic upgrade head

# 5. Create endpoint in app/api/v1/
vim app/api/v1/new_feature.py

# 6. Add to router in app/api/v1/router.py

# 7. Write tests
vim tests/api/test_new_feature.py

# 8. Run tests
uv run pytest tests/api/test_new_feature.py
```

---

## File Structure

```
shuushuu-api/
├── .env                    # Environment variables (don't commit!)
├── .gitignore
├── pyproject.toml          # Dependencies and config
├── docker-compose.yml      # MySQL + Redis
├── Dockerfile
├── README.md
│
├── alembic/                # Database migrations
│   ├── env.py
│   └── versions/
│
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app
│   ├── config.py          # Settings
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── images.py
│   │       └── ...
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py    # SQLAlchemy setup
│   │   ├── security.py    # JWT, passwords
│   │   └── cache.py       # Redis caching
│   │
│   ├── models/            # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── image.py
│   │   └── ...
│   │
│   ├── schemas/           # Pydantic models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── image.py
│   │   └── ...
│   │
│   ├── services/          # Business logic
│   │   ├── __init__.py
│   │   ├── image_processor.py
│   │   ├── tag_service.py
│   │   └── ...
│   │
│   └── utils/             # Helper functions
│       ├── __init__.py
│       └── ...
│
├── tests/
│   ├── conftest.py
│   ├── api/
│   ├── models/
│   └── services/
│
└── storage/               # Image files
    ├── fullsize/
    └── thumbs/
```

---

## Comparing to Your PHP Code

### PHP (current)
```php
<?php
require("common.php");

$image_id = (int)$_GET['id'];
$result = $db->query("SELECT * FROM images WHERE image_id = $image_id");
$image = $result->fetch_assoc();

header('Content-Type: application/json');
echo json_encode($image);
?>
```

### FastAPI (new)
```python
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.image import Image
from app.schemas.image import ImageResponse

app = FastAPI()

@app.get("/images/{image_id}", response_model=ImageResponse)
async def get_image(
    image_id: int,  # Validated automatically
    db: AsyncSession = Depends(get_db)
) -> ImageResponse:
    result = await db.execute(
        select(Image).where(Image.image_id == image_id)
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(404, "Image not found")
    return image  # Serialized automatically
```

**Key improvements:**
- ✅ Type safety (image_id must be int)
- ✅ Auto validation (returns 422 if invalid)
- ✅ Auto serialization (ImageResponse schema)
- ✅ Auto documentation (appears in /docs)
- ✅ Dependency injection (db session managed)
- ✅ Async/await (better concurrency)

---

## Next Steps

### Week 1: Learn & Setup
- [x] Set up development environment
- [ ] Complete FastAPI tutorial: https://fastapi.tiangolo.com/tutorial/
- [ ] Build a simple CRUD API (practice)
- [ ] Understand async/await basics

### Week 2-3: Authentication
- [ ] Create User model
- [ ] Registration endpoint
- [ ] Login endpoint (JWT tokens)
- [ ] Protected routes with Depends(get_current_user)
- [ ] Write tests

### Week 4-6: Images
- [ ] Image model
- [ ] Upload endpoint with BackgroundTasks
- [ ] Image processing (thumbnails)
- [ ] List/search endpoints
- [ ] IQDB integration

### Week 7-9: Tags & Social
- [ ] Tag system
- [ ] Favorites
- [ ] Ratings
- [ ] Comments

---

## When to Upgrade Components

### BackgroundTasks → Arq
Upgrade when you experience:
- Tasks failing silently
- Need retry logic
- Processing >30 second tasks
- Need scheduled/cron jobs

**Migration effort:** 1-2 days

### MySQL → PostgreSQL
Consider if you need:
- Advanced JSON queries
- Full-text search built-in
- Better concurrent writes
- PostGIS for geospatial

**Migration effort:** 2-3 days (pgloader makes it easy)

---

## Troubleshooting

### uv not found
```bash
# Make sure it's in PATH
source $HOME/.cargo/env

# Or install globally
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### MySQL connection refused
```bash
# Check if MySQL is running
docker-compose ps mysql

# View logs
docker-compose logs mysql

# Restart if needed
docker-compose restart mysql
```

### Alembic can't connect
```bash
# Make sure you're using the SYNC URL in alembic.ini
# mysql+pymysql://  NOT  mysql+aiomysql://
```

### Import errors
```bash
# Make sure you're in project directory
cd /path/to/shuushuu-api

# Use uv run
uv run python script.py
```

---

## Resources

- **uv docs**: https://docs.astral.sh/uv/
- **FastAPI tutorial**: https://fastapi.tiangolo.com/tutorial/
- **SQLAlchemy with async**: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- **Alembic**: https://alembic.sqlalchemy.org/

---

## Summary

**You now have:**
✅ Modern Python project with uv  
✅ FastAPI with auto-generated docs  
✅ MySQL database (familiar!)  
✅ Simple async tasks (BackgroundTasks)  
✅ Docker development environment  
✅ Clear path to production  

**You avoided:**
❌ Database migration complexity  
❌ Celery overhead  
❌ Poetry slowness  
❌ 6+ weeks of migration work  

**Start coding!** 🚀

Begin with authentication (Week 2-3), then move to images (Week 4-6).
Use the `example_image_upload.py` as a reference for how to structure
your endpoints with BackgroundTasks.
