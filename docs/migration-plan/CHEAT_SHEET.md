# Shuushuu FastAPI - Command Reference Cheat Sheet

## Quick Links
📁 **All Files**: [View README](computer:///mnt/user-data/outputs/README.md)  
🚀 **Start Here**: [Getting Started Guide](computer:///mnt/user-data/outputs/GETTING_STARTED.md)  
🗺️ **Full Plan**: [Modernization Roadmap](computer:///mnt/user-data/outputs/MODERNIZATION_ROADMAP.md)  
📦 **Docker Setup**: [docker-compose.yml](computer:///mnt/user-data/outputs/docker-compose.yml)

## Initial Setup Commands

```bash
# Create project directory
mkdir shuushuu-api && cd shuushuu-api

# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Initialize project
poetry init

# Install dependencies
poetry add fastapi[all] sqlalchemy[asyncio] alembic psycopg2-binary \
  redis celery[redis] pillow python-multipart python-jose[cryptography] \
  passlib[bcrypt] pydantic-settings uvicorn[standard] structlog httpx

poetry add --group dev pytest pytest-asyncio pytest-cov black ruff mypy
```

## Docker Commands

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api
docker-compose logs -f celery-worker

# Stop all services
docker-compose down

# Rebuild after changes
docker-compose up -d --build

# Check service status
docker-compose ps

# Access database
docker-compose exec postgres psql -U shuushuu -d shuushuu

# Access Redis CLI
docker-compose exec redis redis-cli
```

## Development Commands

```bash
# Run API locally
poetry run uvicorn app.main:app --reload --port 8000

# Run with debugger
poetry run uvicorn app.main:app --reload --log-level debug

# Run Celery worker
poetry run celery -A app.tasks.celery_app worker --loglevel=info

# Run Celery beat (scheduled tasks)
poetry run celery -A app.tasks.celery_app beat --loglevel=info

# Monitor Celery with Flower
poetry run celery -A app.tasks.celery_app flower
# Then open http://localhost:5555
```

## Database Commands

```bash
# Initialize Alembic
poetry run alembic init alembic

# Create new migration
poetry run alembic revision --autogenerate -m "Description of changes"

# Apply migrations
poetry run alembic upgrade head

# Rollback migration
poetry run alembic downgrade -1

# View migration history
poetry run alembic history

# Current revision
poetry run alembic current

# Connect to database
docker-compose exec postgres psql -U shuushuu -d shuushuu
# Or use Adminer at http://localhost:8080
```

## Testing Commands

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=app --cov-report=html

# Run specific test file
poetry run pytest tests/test_api/test_users.py

# Run specific test
poetry run pytest tests/test_api/test_users.py::test_create_user

# Run tests matching pattern
poetry run pytest -k "test_image"

# Show print statements
poetry run pytest -s

# Stop on first failure
poetry run pytest -x

# Run in parallel
poetry run pytest -n auto
```

## Code Quality Commands

```bash
# Format code with Black
poetry run black app/

# Check formatting
poetry run black --check app/

# Lint with Ruff
poetry run ruff check app/

# Fix linting issues
poetry run ruff check --fix app/

# Type check with mypy
poetry run mypy app/

# Run all checks
poetry run black app/ && poetry run ruff check app/ && poetry run mypy app/
```

## API Testing Commands

```bash
# Health check
curl http://localhost:8000/health

# Register user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@example.com","password":"Test123!"}'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"Test123!"}'

# Access protected endpoint (replace TOKEN)
curl http://localhost:8000/api/v1/profile \
  -H "Authorization: Bearer TOKEN"

# Upload image
curl -X POST http://localhost:8000/api/v1/images \
  -H "Authorization: Bearer TOKEN" \
  -F "file=@/path/to/image.jpg" \
  -F "tags=tag1,tag2"

# Search images
curl "http://localhost:8000/api/v1/images?page=1&page_size=15"
```

## Common Development Tasks

### Create a New Model
```bash
# 1. Add model to app/models/
vim app/models/new_model.py

# 2. Create Pydantic schemas
vim app/schemas/new_model.py

# 3. Create migration
poetry run alembic revision --autogenerate -m "Add new_model table"

# 4. Apply migration
poetry run alembic upgrade head

# 5. Create API endpoints
vim app/api/v1/new_endpoint.py

# 6. Add to router
vim app/api/v1/router.py

# 7. Write tests
vim tests/test_api/test_new_endpoint.py

# 8. Run tests
poetry run pytest tests/test_api/test_new_endpoint.py
```

### Add a New Endpoint
```python
# app/api/v1/images.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.image import ImageResponse

router = APIRouter()

@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(
    image_id: int,
    db: AsyncSession = Depends(get_db)
):
    # Implementation
    pass
```

### Debug Database Queries
```python
# Enable SQL logging in config.py
DB_ECHO = True

# Or temporarily in code
from sqlalchemy import create_engine
engine = create_engine(url, echo=True)
```

## Migration Commands

```bash
# Export data from MySQL
mysqldump -u user -p shuushuu > backup.sql

# Run migration script
poetry run python migrations/php_to_postgres/migrate_all.py

# Verify migration
poetry run python scripts/verify_migration.py

# Sync files to S3 (if using)
poetry run python scripts/migrate_to_s3.py
```

## Production Deployment

```bash
# Build production images
docker-compose -f docker-compose.prod.yml build

# Start production services
docker-compose -f docker-compose.prod.yml up -d

# View logs
docker-compose -f docker-compose.prod.yml logs -f

# Scale workers
docker-compose -f docker-compose.prod.yml up -d --scale celery-worker=4
```

## Monitoring & Debugging

```bash
# View API logs
docker-compose logs -f api

# View Celery logs
docker-compose logs -f celery-worker

# Monitor Redis
docker-compose exec redis redis-cli
> MONITOR

# Check API metrics
curl http://localhost:8000/metrics

# Access Flower dashboard
open http://localhost:5555

# Database queries
docker-compose exec postgres psql -U shuushuu -d shuushuu -c "SELECT * FROM pg_stat_activity;"

# Redis info
docker-compose exec redis redis-cli INFO
```

## Quick Fixes

### Port already in use
```bash
# Find process using port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
```

### Database connection issues
```bash
# Restart PostgreSQL
docker-compose restart postgres

# Check if it's running
docker-compose ps postgres

# View logs
docker-compose logs postgres
```

### Clear Redis cache
```bash
docker-compose exec redis redis-cli FLUSHALL
```

### Reset database
```bash
# Drop and recreate
docker-compose down -v
docker-compose up -d postgres
poetry run alembic upgrade head
```

## Environment Variables

```bash
# Development .env
cat > .env << EOF
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://shuushuu:password@localhost:5432/shuushuu
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
SECRET_KEY=your-secret-key-here-min-32-chars
DEBUG=True
EOF
```

## Useful Python Snippets

### Create a user programmatically
```python
from app.core.security import get_password_hash
from app.models import User

user = User(
    username="admin",
    email="admin@example.com",
    password_hash=get_password_hash("AdminPassword123"),
    permission_level=4  # Admin
)
```

### Query images with tags
```python
from sqlalchemy import select
from app.models import Image, TagLink, Tag

stmt = select(Image).join(TagLink).join(Tag).where(Tag.title == "anime")
result = await db.execute(stmt)
images = result.scalars().all()
```

### Calculate Bayesian rating
```python
from sqlalchemy import func, select
from app.models import Image, ImageRating

# Get average rating count
avg_count = await db.scalar(
    select(func.avg(func.count(ImageRating.user_id)))
    .group_by(ImageRating.image_id)
)

# Get average rating
avg_rating = await db.scalar(select(func.avg(Image.rating)))

# Calculate for specific image
bayesian = (avg_count * avg_rating + num_ratings * rating) / (avg_count + num_ratings)
```

## Keyboard Shortcuts (VS Code)

```
Ctrl/Cmd + Shift + P  - Command palette
Ctrl/Cmd + `          - Toggle terminal
F5                    - Start debugging
Shift + F5            - Stop debugging
Ctrl/Cmd + Shift + F  - Search in files
Ctrl/Cmd + P          - Quick file open
```

## Git Workflow

```bash
# Start new feature
git checkout -b feature/image-upload

# Make changes and commit
git add .
git commit -m "feat: implement image upload endpoint"

# Push to remote
git push origin feature/image-upload

# Create pull request (on GitHub/GitLab)

# After review, merge and clean up
git checkout main
git pull
git branch -d feature/image-upload
```

## Performance Tips

```python
# Use select_related for N+1 queries
stmt = select(Image).options(selectinload(Image.uploader))

# Batch database operations
db.add_all([item1, item2, item3])
await db.commit()

# Use background tasks for slow operations
@app.post("/images")
async def create_image(background_tasks: BackgroundTasks):
    background_tasks.add_task(process_image, image_id)

# Cache expensive operations
from app.core.cache import cache

@cache(ttl=300)
async def get_popular_images():
    # Expensive query
    pass
```

## Troubleshooting

### Import errors
```bash
# Make sure you're in the project directory
cd /path/to/shuushuu-api

# Activate Poetry shell
poetry shell

# Or prefix with poetry run
poetry run python script.py
```

### Async errors
```python
# Wrong - don't do this
def my_function(db: AsyncSession):
    result = db.execute(query)  # Missing await!

# Right - always await
async def my_function(db: AsyncSession):
    result = await db.execute(query)  # Correct
```

### Database migration conflicts
```bash
# Reset migrations
rm -rf alembic/versions/*
poetry run alembic revision --autogenerate -m "Initial schema"
```

## Resources

- 📚 FastAPI Docs: https://fastapi.tiangolo.com
- 📚 SQLAlchemy Docs: https://docs.sqlalchemy.org
- 📚 Alembic Docs: https://alembic.sqlalchemy.org
- 📚 Pydantic Docs: https://docs.pydantic.dev
- 📚 Celery Docs: https://docs.celeryproject.org

## Quick Reference

| Task | Command |
|------|---------|
| Start dev server | `poetry run uvicorn app.main:app --reload` |
| Run tests | `poetry run pytest` |
| Format code | `poetry run black app/` |
| Create migration | `poetry run alembic revision --autogenerate -m "msg"` |
| Apply migrations | `poetry run alembic upgrade head` |
| Start Docker | `docker-compose up -d` |
| View logs | `docker-compose logs -f api` |
| Open API docs | `open http://localhost:8000/docs` |
| Run Celery | `poetry run celery -A app.tasks.celery_app worker` |
| Monitor tasks | `open http://localhost:5555` (Flower) |

---

Keep this cheat sheet handy as you develop! 🚀
