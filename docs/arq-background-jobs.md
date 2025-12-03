# Background Jobs with ARQ

This application uses [arq](https://arq-docs.helpmanual.io/) for reliable background job processing.

## Architecture

- **FastAPI app**: Enqueues jobs via `app.tasks.queue.enqueue_job()`
- **ARQ worker**: Processes jobs in separate process/container
- **Redis**: Job queue storage (database 1, caching uses database 0)

## Running the Worker

### Development (local)

```bash
./scripts/run-worker.sh
```

### Docker Compose

Worker starts automatically with `docker-compose up`.

### Production

```bash
uv run arq app.tasks.worker.WorkerSettings
```

## Available Jobs

### Image Processing

- `create_thumbnail`: Generate thumbnail (250x200)
- `create_variant`: Generate medium/large variants
- `add_to_iqdb`: Index image in IQDB

### Ratings

- `recalculate_rating`: Update Bayesian rating

## Job Configuration

- **Max tries**: 3 attempts per job
- **Timeout**: 5 minutes per job
- **Retry backoff**: Exponential (5s, 10s, 15s...)
- **Concurrency**: 10 jobs at once

## Monitoring

### View worker logs

```bash
docker-compose logs arq-worker --follow
```

### Check Redis queue

```bash
docker-compose exec redis redis-cli -n 1
> KEYS arq:*
> HGETALL arq:job:JOBID
```

### Redis Commander UI

Open http://localhost:8081 to view queues in browser.

## Adding New Jobs

1. Create job function in `app/tasks/*_jobs.py`
2. Register in `app/tasks/worker.py` functions list
3. Enqueue from API: `await enqueue_job("job_name", arg1=val1)`

Example:

```python
# Define job
async def my_new_job(ctx: dict, param: str) -> dict:
    logger.info("job_running", param=param)
    return {"success": True}

# Register in worker.py
from arq.worker import func

functions = [
    func(my_new_job, max_tries=3),
]

# Enqueue from API
await enqueue_job("my_new_job", param="value")
```

## Troubleshooting

**Worker not starting?**
- Check Redis is running: `docker-compose ps redis`
- Check ARQ_REDIS_URL in .env
- Check worker logs: `docker-compose logs arq-worker`

**Jobs not processing?**
- Verify job is enqueued: Redis Commander or `redis-cli`
- Check worker is running: `docker-compose ps arq-worker`
- Check for errors in worker logs

**Job keeps retrying?**
- Check worker logs for error details
- Verify job dependencies (files exist, services available)
- Check max_tries configuration
