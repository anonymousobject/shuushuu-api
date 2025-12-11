# Updated Modernization Strategy - MySQL + uv

## Key Updates Based on Your Feedback

### 1. Package Manager: uv instead of Poetry ✅

**Why uv is great:**
- Much faster than Poetry (10-100x in many cases)
- Compatible with pip and requirements.txt
- Simpler mental model
- Less overhead
- Growing adoption in the Python community

### 2. Database: Stay with MySQL/MariaDB ✅

**Good news: This is totally fine!**

SQLAlchemy works excellently with MySQL. Here's the reality:

**Pros of staying with MySQL:**
- ✅ Zero learning curve - you already know it
- ✅ No data migration needed initially
- ✅ Can use existing database during parallel running
- ✅ Proven at scale for your use case
- ✅ Can migrate to PostgreSQL later if needed (SQLAlchemy makes this easy)
- ✅ Focus on backend/frontend instead of database migration

**Cons (minor):**
- ⚠️ PostgreSQL has better JSON support (but you don't seem to need it)
- ⚠️ PostgreSQL has some advanced features (but you're not using them)
- ⚠️ Some argue PostgreSQL is "better" (but MySQL is perfectly fine for your needs)

**Bottom line:** Stay with MySQL. It's the right choice for your situation.

### 3. Task Queue: Simpler alternatives to Celery

You're right to question Celery - it's powerful but can be overkill. Here are better options:

#### Option A: **FastAPI BackgroundTasks** (Recommended for start)
```python
from fastapi import BackgroundTasks

@app.post("/images")
async def upload_image(
    file: UploadFile,
    background_tasks: BackgroundTasks
):
    # Save image immediately
    image = await save_image(file)
    
    # Process thumbnails in background (same process)
    background_tasks.add_task(create_thumbnails, image.id)
    background_tasks.add_task(add_to_iqdb, image.id)
    
    return image
```

**Pros:**
- ✅ Simple - no external dependencies
- ✅ No extra services to run
- ✅ Perfect for quick async tasks
- ✅ Built into FastAPI

**Cons:**
- ⚠️ Tasks run in same process (no distribution)
- ⚠️ Lost if server restarts mid-task
- ⚠️ No retry mechanism
- ⚠️ Not suitable for long-running tasks (>30s)

**Use for:** Thumbnails, IQDB indexing, sending emails

#### Option B: **Arq** (Recommended for growth)
```python
# Much simpler than Celery, similar to Beanstalk simplicity
from arq import create_pool
from arq.connections import RedisSettings

async def process_image(ctx, image_id: int):
    """Process image thumbnails"""
    # Your processing logic
    await create_thumbnails(image_id)
    await add_to_iqdb(image_id)

# Queue a job
await redis.enqueue_job('process_image', image_id)
```

**Pros:**
- ✅ Simple API (simpler than Celery)
- ✅ Uses Redis (which you need anyway for caching)
- ✅ Async/await native
- ✅ Automatic retries
- ✅ Scheduled/cron jobs
- ✅ Can run in separate worker processes

**Cons:**
- ⚠️ Smaller community than Celery
- ⚠️ Less features than Celery (but you probably don't need them)

**Use for:** All async tasks when you outgrow BackgroundTasks

#### Option C: **Celery** (If you need enterprise features)

Only choose Celery if you need:
- Multiple queue priorities
- Complex routing
- Task chains and workflows
- Canvas (map/reduce)
- Very large scale (thousands of tasks/sec)

**For Shuushuu, I recommend:**
1. **Start with BackgroundTasks** - covers 80% of your needs
2. **Upgrade to Arq** when you need reliability/retries
3. **Never need Celery** - your scale doesn't require it

---

## Updated Project Setup

### Using uv

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create project
mkdir shuushuu-api && cd shuushuu-api

# Initialize Python 3.11+ project
uv init --python 3.11

# Create virtual environment
uv venv

# Activate it
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate  # Windows

# Add dependencies
uv add fastapi[standard] \
  sqlalchemy[asyncio] \
  aiomysql \
  alembic \
  redis \
  arq \
  pillow \
  python-multipart \
  python-jose[cryptography] \
  passlib[bcrypt] \
  pydantic-settings \
  structlog \
  httpx

# Add dev dependencies
uv add --dev pytest pytest-asyncio pytest-cov black ruff mypy

# Run commands (uv automatically manages the venv)
uv run uvicorn app.main:app --reload
uv run pytest
uv run alembic upgrade head
```

### MySQL Configuration

```python
# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # MySQL connection string
    DATABASE_URL: str = "mysql+aiomysql://shuushuu:password@localhost:3306/shuushuu?charset=utf8mb4"
    
    # All other settings same as before
    ...

settings = Settings()
```

```python
# app/core/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# Works perfectly with MySQL!
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    pool_recycle=3600,  # Recycle connections after 1 hour (MySQL specific)
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass
```

### Using BackgroundTasks (Start Simple)

```python
# app/api/v1/images.py
from fastapi import APIRouter, UploadFile, BackgroundTasks, Depends
from app.services.image_processor import create_thumbnails, add_to_iqdb

router = APIRouter()

@router.post("/")
async def upload_image(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Validate and save image record to database
    image = await save_image_to_db(file, current_user, db)
    
    # 2. Save full-size image to disk
    filepath = await save_image_file(file, image)
    
    # 3. Queue background tasks (non-blocking)
    background_tasks.add_task(create_thumbnails, image.image_id, filepath)
    background_tasks.add_task(add_to_iqdb, image.image_id, filepath)
    
    # 4. Return immediately
    return image
```

### Upgrade to Arq When Needed

```python
# app/tasks/worker.py
"""
Arq worker - only add this when BackgroundTasks isn't enough
"""
from arq import create_pool
from arq.connections import RedisSettings
from app.services.image_processor import create_thumbnails_sync

async def process_image_thumbnails(ctx, image_id: int, filepath: str):
    """Background task with retry support"""
    await create_thumbnails_sync(image_id, filepath)

async def add_image_to_iqdb(ctx, image_id: int, filepath: str):
    """Add image to IQDB index"""
    await add_to_iqdb_sync(image_id, filepath)

class WorkerSettings:
    """Arq worker configuration"""
    functions = [process_image_thumbnails, add_image_to_iqdb]
    redis_settings = RedisSettings(host='localhost', port=6379)
    
    # Simple retry logic
    max_tries = 3
    keep_result = 3600  # Keep results for 1 hour

# Run worker: arq app.tasks.worker.WorkerSettings
```

---

## MySQL-Specific Considerations

### 1. Connection String Format
```python
# Async MySQL (aiomysql)
DATABASE_URL = "mysql+aiomysql://user:pass@host:port/dbname?charset=utf8mb4"

# Sync MySQL (if needed for migrations)
DATABASE_URL_SYNC = "mysql+pymysql://user:pass@host:port/dbname?charset=utf8mb4"
```

### 2. Character Set (Important!)
Always use `utf8mb4` for proper emoji/unicode support:
```sql
ALTER DATABASE shuushuu CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE images CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. Connection Pool Settings
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,  # MySQL drops idle connections after 8 hours by default
    pool_pre_ping=True,  # Verify connections before use
)
```

### 4. Migration from MySQL to PostgreSQL (Future)
If you ever want to migrate, SQLAlchemy makes it relatively easy:

```python
# Your models stay the same!
# Just change the connection string:

# OLD
DATABASE_URL = "mysql+aiomysql://..."

# NEW  
DATABASE_URL = "postgresql+asyncpg://..."

# SQLAlchemy handles the differences
```

**Migration effort:** 
- 90% of your code stays the same
- May need to adjust a few queries (rare cases)
- Need to migrate data (use pgloader - it's excellent)
- Maybe 2-3 days of work total

---

## Updated Docker Compose

```yaml
# docker-compose.yml - MySQL version
version: '3.8'

services:
  # MySQL Database
  mysql:
    image: mysql:8.0
    container_name: shuushuu-mysql
    environment:
      MYSQL_ROOT_PASSWORD: root_password
      MYSQL_DATABASE: shuushuu
      MYSQL_USER: shuushuu
      MYSQL_PASSWORD: shuushuu_dev_password
    command: --default-authentication-plugin=mysql_native_password --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "shuushuu", "-pshuushuu_dev_password"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis (for caching + Arq if needed)
  redis:
    image: redis:7-alpine
    container_name: shuushuu-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  # FastAPI Application
  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: shuushuu-api
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    ports:
      - "8000:8000"
    environment:
      - ENVIRONMENT=development
      - DATABASE_URL=mysql+aiomysql://shuushuu:shuushuu_dev_password@mysql:3306/shuushuu?charset=utf8mb4
      - REDIS_URL=redis://redis:6379/0
      - SECRET_KEY=dev_secret_key_change_in_production_min_32_chars
      - IQDB_HOST=iqdb
      - IQDB_PORT=5588
    volumes:
      - .:/app
      - ./storage:/shuushuu/images
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  # Arq Worker (only when needed - comment out to start)
  # arq-worker:
  #   build:
  #     context: .
  #     dockerfile: Dockerfile
  #   container_name: shuushuu-arq-worker
  #   command: arq app.tasks.worker.WorkerSettings
  #   environment:
  #     - ENVIRONMENT=development
  #     - DATABASE_URL=mysql+aiomysql://shuushuu:shuushuu_dev_password@mysql:3306/shuushuu?charset=utf8mb4
  #     - REDIS_URL=redis://redis:6379/0
  #   volumes:
  #     - .:/app
  #     - ./storage:/shuushuu/images
  #   depends_on:
  #     - redis
  #     - mysql
  #   restart: unless-stopped

  # IQDB (Image similarity)
  iqdb:
    image: thewhitetulip/iqdb:latest
    container_name: shuushuu-iqdb
    ports:
      - "5588:5588"
    volumes:
      - iqdb_data:/data
    restart: unless-stopped

  # Adminer (Database management)
  adminer:
    image: adminer:latest
    container_name: shuushuu-adminer
    ports:
      - "8080:8080"
    environment:
      - ADMINER_DEFAULT_SERVER=mysql
    depends_on:
      - mysql
    restart: unless-stopped

volumes:
  mysql_data:
  redis_data:
  iqdb_data:
```

---

## Updated Quick Start

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create project
mkdir shuushuu-api && cd shuushuu-api
uv init --python 3.11

# 3. Add dependencies
uv add fastapi[standard] sqlalchemy[asyncio] aiomysql alembic \
  redis arq pillow python-multipart python-jose[cryptography] \
  passlib[bcrypt] pydantic-settings structlog httpx

uv add --dev pytest pytest-asyncio pytest-cov black ruff mypy

# 4. Create project structure
mkdir -p app/{api/v1,core,models,schemas,services,tasks,utils}

# 5. Start Docker (MySQL + Redis)
docker-compose up -d

# 6. Wait for MySQL to be ready
docker-compose logs -f mysql  # Wait for "ready for connections"

# 7. Create database schema
uv run alembic init alembic
# Edit alembic/env.py to use async SQLAlchemy + MySQL
uv run alembic revision --autogenerate -m "Initial schema"
uv run alembic upgrade head

# 8. Run the API
uv run uvicorn app.main:app --reload

# 9. Test it
curl http://localhost:8000/health
open http://localhost:8000/docs
```

---

## Migration Strategy (No Database Migration Initially!)

### Phase 1: Keep Everything Simple
```
Week 1-6: Set up FastAPI with MySQL (your existing database!)
- Use existing MySQL database
- Use BackgroundTasks for async work
- Focus on building API endpoints
```

### Phase 2: Upgrade Async Tasks (If Needed)
```
Week 7+: Add Arq only if BackgroundTasks limitations hit you
- Still using MySQL
- Add Redis-based task queue
- Better reliability for image processing
```

### Phase 3: Maybe PostgreSQL (Optional, Far Future)
```
Month 6+: Consider PostgreSQL only if you need advanced features
- Use pgloader for data migration (super easy)
- Change connection string
- Test thoroughly
- Done!
```

---

## Recommendation Summary

### For Your Situation:

1. **Package Manager: uv** ✅
   - Faster, simpler, modern
   - Great choice

2. **Database: MySQL** ✅
   - Stay with what you know
   - Zero migration effort
   - Focus on backend/frontend
   - Can change later if needed

3. **Task Queue: BackgroundTasks → Arq** ✅
   - Start with BackgroundTasks (simplest)
   - Upgrade to Arq when you need reliability
   - Skip Celery (overkill for your needs)

### Updated Timeline:

**No database migration = Save 4-6 weeks!**

- ~~Phase 1 (6 weeks)~~ → **Phase 1 (3-4 weeks)** - Foundation
  - No database migration needed!
  - Just build models from existing schema
  - Use BackgroundTasks for now

- Phase 2 (10 weeks) - Same - Core API
- Phase 3 (6 weeks) - Same - Admin & Moderation  
- ~~Phase 4 (6 weeks)~~ → **Phase 4 (3-4 weeks)** - Deployment
  - No data migration needed!
  - Just switch over to new API

**New total: 22-28 weeks instead of 28-40 weeks!**

---

## Why This Approach is Better for You

✅ **Faster**: Save 6-12 weeks by skipping database migration  
✅ **Simpler**: Use familiar MySQL, simple BackgroundTasks  
✅ **Focused**: Spend time on backend/frontend, not database  
✅ **Flexible**: Can upgrade tasks queue or database later  
✅ **Modern**: Still get all FastAPI benefits  
✅ **Practical**: Right tool for the job, not over-engineering  

Your instincts are correct - stay with MySQL and keep tasks simple!
