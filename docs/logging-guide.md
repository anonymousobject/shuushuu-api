# Logging System Guide

## Overview

The Shuushuu API uses a structured logging system that provides:

- **JSON logging in production** for log aggregation and analysis
- **Pretty console output in development** for easy debugging
- **Request tracking** via unique request IDs across all operations
- **Context binding** for background tasks and async operations
- **Consistent logging format** across all endpoints

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                      │
├─────────────────────────────────────────────────────────────┤
│  RequestLoggingMiddleware (generates UUID request_id)       │
│         ↓                                                    │
│  set_request_context(request_id) → ContextVar               │
│         ↓                                                    │
│  Endpoint Handler (logs with automatic request_id)          │
│         ↓                                                    │
│  Background Task (bind_context for task-specific vars)      │
│         ↓                                                    │
│  clear_request_context() on request completion              │
└─────────────────────────────────────────────────────────────┘
```

## Libraries Used

### 1. **structlog** (v24.1.0+)

**What it does**: Provides structured logging where log messages are dictionaries rather than plain strings.

**Why we use it**:
- Makes logs machine-readable (easy to parse and search)
- Supports context binding (attach data to all subsequent logs)
- Works seamlessly with both console and JSON output
- Thread-safe and async-safe with context variables

**Example output**:
```python
# Development (console):
2025-11-17 10:23:45 [info] image_upload_started user_id=123 filename=anime.png request_id=abc-123

# Production (JSON):
{"timestamp": "2025-11-17T10:23:45Z", "level": "info", "event": "image_upload_started", "user_id": 123, "filename": "anime.png", "request_id": "abc-123"}
```

### 2. **python-json-logger** (v2.0.7+)

**What it does**: Formats Python logging records as JSON.

**Why we use it**:
- Works with standard Python logging (no rewrite needed)
- Ensures consistent JSON structure in production
- Compatible with log aggregation tools (ELK, Datadog, etc.)
- Handles exceptions and stack traces properly

## Core Components

### 1. Configuration (`app/core/logging.py`)

The central logging configuration module. Import this once at application startup:

```python
from app.core.logging import configure_logging

# Call once when app starts
configure_logging()
```

**What it does**:
- Detects environment (development vs production)
- Sets up appropriate formatters (pretty console vs JSON)
- Configures log levels (DEBUG in dev, INFO in prod)
- Initializes context variables for request tracking

### 2. Getting a Logger

In any module that needs logging:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)
```

**Best practice**: Use `__name__` so logs show the module path (e.g., `app.api.v1.images`).

### 3. Request Context Tracking

The middleware automatically tracks each request:

```python
# In app/main.py
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        set_request_context(request_id)  # All logs in this request get this ID
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            clear_request_context()  # Clean up after request
```

### 4. Context Binding for Background Tasks

Background tasks run outside the request context, so bind their own context:

```python
from app.core.logging import bind_context

def background_task(image_id: int):
    # Bind context for all logs in this task
    bind_context(task="thumbnail_generation", image_id=image_id)

    logger.info("thumbnail_generation_started")  # Automatically includes task and image_id
    # ... do work ...
    logger.info("thumbnail_generation_completed")
```

## Usage Examples

### Basic Logging in Endpoints

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

@router.post("/items/")
async def create_item(item: ItemCreate, user_id: int = Depends(get_current_user_id)):
    # Simple info log
    logger.info("item_creation_started", user_id=user_id, item_type=item.type)

    try:
        # Business logic
        new_item = await service.create_item(item)

        # Success log with relevant data
        logger.info(
            "item_created",
            item_id=new_item.id,
            item_type=new_item.type,
            user_id=user_id
        )

        return new_item

    except ValueError as e:
        # Warning for expected errors
        logger.warning(
            "item_creation_validation_failed",
            error=str(e),
            user_id=user_id,
            item_data=item.model_dump()
        )
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        # Error for unexpected failures
        logger.error(
            "item_creation_failed",
            error=str(e),
            error_type=type(e).__name__,
            user_id=user_id,
            exc_info=True  # Include stack trace
        )
        raise
```

### Logging in Background Tasks

```python
from fastapi import BackgroundTasks
from app.core.logging import get_logger, bind_context

logger = get_logger(__name__)

def process_upload(file_path: str, user_id: int):
    """Background task to process uploaded file"""
    # Bind context for this task
    bind_context(
        task="file_processing",
        file_path=file_path,
        user_id=user_id
    )

    logger.info("processing_started")

    try:
        # Processing logic
        result = expensive_operation(file_path)

        logger.info("processing_completed", result_size=len(result))

    except Exception as e:
        logger.error("processing_failed", error=str(e), exc_info=True)
        raise

@router.post("/upload/")
async def upload_file(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id)
):
    # Save file
    file_path = save_file(file)

    # Queue background task
    background_tasks.add_task(process_upload, file_path, user_id)

    logger.info("upload_queued", file_path=file_path, user_id=user_id)

    return {"message": "Upload queued for processing"}
```

### Logging Database Operations

```python
@router.get("/items/{item_id}")
async def get_item(item_id: int, db: AsyncSession = Depends(get_db)):
    logger.info("item_fetch_started", item_id=item_id)

    try:
        result = await db.execute(
            select(Item).where(Item.id == item_id)
        )
        item = result.scalar_one_or_none()

        if not item:
            logger.warning("item_not_found", item_id=item_id)
            raise HTTPException(status_code=404, detail="Item not found")

        logger.info("item_fetched", item_id=item_id, item_type=item.type)
        return item

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "item_fetch_failed",
            item_id=item_id,
            error=str(e),
            exc_info=True
        )
        raise
```

## Expanding to Other Routes

### Step 1: Import the Logger

At the top of your route file:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)
```

### Step 2: Add Logging to Key Operations

Add logs at these points:

1. **Operation start**: When the endpoint begins processing
2. **Important decisions**: When branching logic occurs
3. **External calls**: Before/after database, API, or file system operations
4. **Success**: When operation completes successfully
5. **Failures**: When errors occur (with context)

### Step 3: Choose Appropriate Log Levels

- **`logger.debug()`**: Detailed debugging info (only in development)
- **`logger.info()`**: Normal operations, successful events
- **`logger.warning()`**: Expected errors (validation failures, not found, etc.)
- **`logger.error()`**: Unexpected errors, system failures

### Step 4: Include Relevant Context

Always include data that helps diagnose issues:

```python
# Good - includes useful context
logger.info("order_created", order_id=123, user_id=456, total_amount=99.99, item_count=3)

# Bad - lacks context
logger.info("Order created")
```

### Complete Example: Adding Logging to a New Route

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.auth import get_current_user_id
from app.core.logging import get_logger
from app.schemas.order import OrderCreate, OrderResponse
from app.models.order import Order

router = APIRouter(prefix="/orders", tags=["orders"])
logger = get_logger(__name__)


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order: OrderCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    """Create a new order"""
    logger.info(
        "order_creation_started",
        user_id=user_id,
        item_count=len(order.items),
        total_amount=order.total
    )

    try:
        # Validate order items
        if not order.items:
            logger.warning("order_creation_failed_empty", user_id=user_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order must contain at least one item"
            )

        # Check inventory
        logger.debug("checking_inventory", item_ids=[item.id for item in order.items])
        # ... inventory check logic ...

        # Create order in database
        db_order = Order(
            user_id=user_id,
            total=order.total,
            status="pending"
        )
        db.add(db_order)
        await db.flush()

        logger.info(
            "order_created_in_db",
            order_id=db_order.id,
            user_id=user_id
        )

        # Add order items
        for item in order.items:
            # ... add items logic ...
            logger.debug("order_item_added", order_id=db_order.id, item_id=item.id)

        await db.commit()
        await db.refresh(db_order)

        logger.info(
            "order_creation_completed",
            order_id=db_order.id,
            user_id=user_id,
            item_count=len(order.items),
            total_amount=order.total
        )

        return db_order

    except HTTPException:
        # Re-raise HTTP exceptions (already logged)
        raise

    except ValueError as e:
        # Validation errors
        logger.warning(
            "order_creation_validation_failed",
            error=str(e),
            user_id=user_id,
            order_data=order.model_dump()
        )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        # Unexpected errors
        logger.error(
            "order_creation_failed",
            error=str(e),
            error_type=type(e).__name__,
            user_id=user_id,
            exc_info=True  # Include full stack trace
        )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create order"
        )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    """Get order by ID"""
    logger.info("order_fetch_started", order_id=order_id, user_id=user_id)

    try:
        result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .where(Order.user_id == user_id)  # Security check
        )
        order = result.scalar_one_or_none()

        if not order:
            logger.warning(
                "order_not_found",
                order_id=order_id,
                user_id=user_id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        logger.info(
            "order_fetched",
            order_id=order_id,
            user_id=user_id,
            status=order.status
        )

        return order

    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            "order_fetch_failed",
            order_id=order_id,
            user_id=user_id,
            error=str(e),
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch order"
        )
```

## Best Practices

### 1. Use Structured Data, Not String Formatting

```python
# Good - structured data
logger.info("user_login", user_id=123, ip_address="1.2.3.4")

# Bad - string formatting (harder to parse)
logger.info(f"User {user_id} logged in from {ip_address}")
```

### 2. Use Consistent Event Names

Use descriptive, snake_case event names:

```python
# Good naming convention
logger.info("image_upload_started")
logger.info("image_processing_completed")
logger.warning("duplicate_image_detected")
logger.error("storage_write_failed")

# Bad - inconsistent or unclear
logger.info("start")
logger.info("imageProcessingComplete")
logger.warning("dup")
```

### 3. Include Relevant IDs

Always include identifiers that help trace operations:

```python
logger.info(
    "payment_processed",
    order_id=order.id,
    user_id=user.id,
    transaction_id=transaction.id,
    amount=payment.amount
)
```

### 4. Don't Log Sensitive Data

Never log passwords, tokens, or PII:

```python
# Bad - logs sensitive data
logger.info("user_created", username=user.username, password=user.password)

# Good - excludes sensitive data
logger.info("user_created", user_id=user.id, username=user.username)
```

### 5. Use `exc_info=True` for Exceptions

Include stack traces for unexpected errors:

```python
try:
    risky_operation()
except Exception as e:
    logger.error(
        "operation_failed",
        error=str(e),
        error_type=type(e).__name__,
        exc_info=True  # Adds full stack trace
    )
    raise
```

### 6. Log at Operation Boundaries

Log when entering/exiting major operations:

```python
logger.info("batch_processing_started", batch_size=len(items))

for item in items:
    process_item(item)

logger.info("batch_processing_completed", batch_size=len(items), success_count=successes)
```

## Development vs Production

### Development Mode (`ENVIRONMENT=development`)

- **Output**: Pretty console with colors
- **Format**: Human-readable with aligned columns
- **Level**: DEBUG (shows all logs)
- **Example**:
  ```
  2025-11-17 10:23:45 [info] image_upload_started user_id=123 filename=anime.png
  2025-11-17 10:23:46 [debug] checking_duplicate md5_hash=abc123def456
  2025-11-17 10:23:47 [info] image_saved image_id=456 file_path=/storage/456.png
  ```

### Production Mode (`ENVIRONMENT=production` or `ENVIRONMENT=staging`)

- **Output**: JSON (one object per line)
- **Format**: Machine-readable for log aggregators
- **Level**: INFO (hides debug logs)
- **Example**:
  ```json
  {"timestamp":"2025-11-17T10:23:45Z","level":"info","event":"image_upload_started","user_id":123,"filename":"anime.png","request_id":"abc-123"}
  {"timestamp":"2025-11-17T10:23:47Z","level":"info","event":"image_saved","image_id":456,"file_path":"/storage/456.png","request_id":"abc-123"}
  ```

### Configuring Environment

Set in `.env` file:

```bash
# Development
ENVIRONMENT=development

# Production
ENVIRONMENT=production
```

## Viewing Logs

### In Development

Logs appear in console with colors:

```bash
docker compose logs -f api
```

### In Production

Logs are JSON format. Use `jq` to filter:

```bash
# View all logs
docker compose logs api | jq '.'

# Filter by event
docker compose logs api | jq 'select(.event == "image_upload_started")'

# Filter by user
docker compose logs api | jq 'select(.user_id == 123)'

# Show only errors
docker compose logs api | jq 'select(.level == "error")'

# Follow specific request
docker compose logs api | jq 'select(.request_id == "abc-123")'
```

## Integration with Log Aggregators

Production JSON logs work with tools like:

### ELK Stack (Elasticsearch, Logstash, Kibana)

```yaml
# logstash.conf
input {
  file {
    path => "/var/log/shuushuu-api/*.log"
    codec => json
  }
}

filter {
  # Logs are already JSON, no parsing needed
}

output {
  elasticsearch {
    hosts => ["elasticsearch:9200"]
    index => "shuushuu-api-%{+YYYY.MM.dd}"
  }
}
```

### Datadog

Logs are automatically parsed with JSON format. Set up tags:

```python
# In app/config.py
LOG_TAGS = f"service:shuushuu-api,env:{ENVIRONMENT}"
```

### CloudWatch Logs

JSON format allows for CloudWatch Insights queries:

```sql
fields @timestamp, event, user_id, @message
| filter event = "image_upload_started"
| sort @timestamp desc
| limit 100
```

## Troubleshooting

### Logs Not Appearing

1. Check log level in `.env`:
   ```bash
   LOG_LEVEL=DEBUG  # Most verbose
   ```

2. Verify logging was configured:
   ```python
   # In app/main.py
   configure_logging()  # Should be called once
   ```

### Request ID Not Showing

Ensure middleware is added:

```python
# In app/main.py
app.add_middleware(RequestLoggingMiddleware)
```

### Background Task Logs Missing Context

Use `bind_context()` in background tasks:

```python
from app.core.logging import bind_context

def background_task():
    bind_context(task="my_task", task_id=123)
    logger.info("task_started")  # Now includes task context
```

### Exception Stack Traces Not Showing

Add `exc_info=True`:

```python
except Exception as e:
    logger.error("error_occurred", error=str(e), exc_info=True)
```

## Summary

- **Use structured logging** with key-value pairs instead of strings
- **Import logger** at the top of each module: `logger = get_logger(__name__)`
- **Log at boundaries**: start, success, errors
- **Include context**: user_id, item_id, etc.
- **Use appropriate levels**: debug, info, warning, error
- **Bind context** for background tasks with `bind_context()`
- **Never log** sensitive data (passwords, tokens)
- **Use `exc_info=True`** for unexpected exceptions

The logging system is now consistently available across all routes and background tasks, providing visibility into API operations for both development debugging and production monitoring.
