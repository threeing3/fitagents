import time
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fast_api.app.core.config import get_settings


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


class Base(DeclarativeBase):
    pass


settings = get_settings()


def _engine_options() -> dict[str, int | bool]:
    options: dict[str, int | bool] = {"pool_pre_ping": True}
    if settings.database_url.startswith("postgresql"):
        options.update(
            {
                "pool_size": settings.db_pool_size,
                "max_overflow": settings.db_max_overflow,
                "pool_timeout": settings.db_pool_timeout_seconds,
            }
        )
    return options


engine = create_engine(settings.database_url, **_engine_options())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 20, delay_seconds: float = 1.5) -> None:
    """Create the vector extension and apply versioned Alembic migrations.

    A metadata fallback is retained for a brand-new local environment where
    Alembic is unavailable. Existing-volume repairs belong in migrations,
    never in an application-startup SQL side channel.
    """

    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with engine.begin() as connection:
                if settings.use_pgvector:
                    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            from fast_api.app.db import models  # noqa: F401

            alembic_ini = Path(__file__).resolve().parents[3] / "alembic.ini"
            try:
                from alembic import command
                from alembic.config import Config
            except ImportError:
                if settings.environment.lower() in {"production", "staging"}:
                    raise
                Base.metadata.create_all(bind=engine)
                return

            if not alembic_ini.is_file():
                if settings.environment.lower() in {"production", "staging"}:
                    raise FileNotFoundError(f"Alembic configuration not found: {alembic_ini}")
                Base.metadata.create_all(bind=engine)
                return

            alembic_cfg = Config(str(alembic_ini))
            command.upgrade(alembic_cfg, "head")
            return
        except Exception as exc:  # pragma: no cover - exercised in Docker startup
            last_error = exc
            time.sleep(delay_seconds)

    raise RuntimeError(f"Database initialization failed: {last_error}")
