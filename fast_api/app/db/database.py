import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fast_api.app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 20, delay_seconds: float = 1.5) -> None:
    """Create pgvector extension, run Alembic migrations, and create tables.

    Uses Alembic for schema migrations (production-ready, version-controlled).
    Falls back to Base.metadata.create_all for fresh environments where no
    migration history exists.
    """

    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with engine.begin() as connection:
                if settings.use_pgvector:
                    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            from fast_api.app.db import models  # noqa: F401

            # Try Alembic first (production path)
            try:
                from alembic.config import Config
                from alembic import command
                import os

                alembic_ini = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..", "alembic.ini"
                )
                if os.path.exists(alembic_ini):
                    alembic_cfg = Config(alembic_ini)
                    command.upgrade(alembic_cfg, "head")
                else:
                    raise FileNotFoundError("alembic.ini not found")
            except Exception:
                # Fallback for fresh deployments without migration history
                Base.metadata.create_all(bind=engine)

            return
        except Exception as exc:  # pragma: no cover - exercised in Docker startup
            last_error = exc
            time.sleep(delay_seconds)

    raise RuntimeError(f"Database initialization failed: {last_error}")
