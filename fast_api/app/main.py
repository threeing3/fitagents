"""AI Fitness Coach API — main application entry point."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Configure LangSmith tracing
os.environ.setdefault("LANGCHAIN_TRACING_V2", os.getenv("LANGCHAIN_TRACING_V2", "false"))
os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGCHAIN_PROJECT", "ai-fitness-coach"))

from fast_api.app.api.coach_platform import coach_router
from fast_api.app.api.memory_api import memory_router
from fast_api.app.api.auth_api import auth_router
from fast_api.app.api.nutrition_api import nutrition_router
from fast_api.app.api.eval_api import eval_router
from fast_api.app.api.feedback_api import feedback_router
from fast_api.app.api.approval_api import approval_router
from fast_api.app.core.config import get_settings
from fast_api.app.core.errors import register_exception_handlers
from fast_api.app.core.metrics import REGISTRY
from fast_api.app.db.database import SessionLocal, init_db
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService

settings = get_settings()

# Rate limiter — keyed by client IP by default
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title=settings.app_name,
    version="2.0",
    description="AI private fitness coach agent with long-term memory and PostgreSQL.",
)

# Register centralized exception handlers (before rate-limit handler so
# the rate-limit handler takes precedence for 429 responses).
register_exception_handlers(app)

# Register rate-limit middleware and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as db:
        FitnessKnowledgeService(db).seed_builtin_knowledge()


@app.get("/health")
@limiter.limit("30/minute")
def health(request: Request) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "chat_model": settings.chat_model,
        "live_model_key_present": settings.has_live_model_key,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "live_embedding_key_present": settings.has_live_embedding_key,
    }


@app.get("/metrics")
def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(content=REGISTRY.generate_latest(), media_type="text/plain; version=0.0.4")


# ---- Per-router rate limits via dependency injection ----
# Each router's endpoints share a bucket via the limiter dependency.
# Decorator-style limits are applied in the individual route files.

app.include_router(coach_router, prefix="/v1", tags=["coach-agent"])
app.include_router(memory_router, prefix="/v1", tags=["memory-system"])
app.include_router(auth_router, tags=["auth"])
app.include_router(nutrition_router)
app.include_router(eval_router, prefix="/v1", tags=["evaluation"])
app.include_router(feedback_router)
app.include_router(approval_router)
