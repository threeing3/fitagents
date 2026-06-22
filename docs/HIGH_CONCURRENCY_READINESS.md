# High-Concurrency Readiness

This project now has a first production-readiness layer for concurrent users and expensive AI workloads.

## Implemented

- Distributed rate-limit configuration through `REDIS_URL`, with local fallback when Redis is unavailable.
- Stricter configurable limits for chat, streaming chat, plan generation, and nutrition recognition.
- Database connection-pool controls: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, and `DB_POOL_TIMEOUT_SECONDS`.
- Persistent `background_tasks` queue for expensive work, starting with async plan generation and evaluation runs.
- `scripts/run_background_worker.py` to consume queued tasks.
- Redis-backed short TTL dashboard cache.
- API, rate-limit, background-task, queue-depth, and database-pool metrics under `/metrics`.
- `locustfile.py` load-test scenarios for health, sessions, dashboard, and async plan generation.

## Local Run

```powershell
docker-compose up -d
python scripts/run_background_worker.py
```

Async plan generation:

```http
POST /v1/plans/generate/async
GET /v1/tasks/{task_id}
```

Async evaluation run:

```http
POST /v1/evals/run/async
GET /v1/tasks/{task_id}
```

## Load Test

```powershell
$env:FITNESS_AUTH_TOKEN="..."
$env:FITNESS_USER_ID="..."
locust -f locustfile.py --host http://127.0.0.1:1015
```

Watch:

- `fitness_api_request_latency_seconds`
- `fitness_rate_limit_rejections_total`
- `fitness_background_task_queue_depth`
- `fitness_background_task_latency_seconds`
- `fitness_cache_hits_total`
- `fitness_llm_request_latency_seconds`

## Next Production Step

The current queue is intentionally database-backed so it is simple and recoverable for this MVP. Image recognition remains synchronous but rate-limited until uploaded image persistence is added. If sustained workload grows, replace the worker internals with Celery or RQ while preserving the API shape and `background_tasks` status contract.
