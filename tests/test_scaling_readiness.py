from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_scaling_settings_defaults_are_available():
    from fast_api.app.core.config import Settings

    settings = Settings(LLM_PROVIDER="offline", EMBEDDING_PROVIDER="offline")

    assert settings.rate_limit_default == "60/minute"
    assert settings.rate_limit_chat == "15/minute"
    assert settings.rate_limit_plan == "10/minute"
    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 20
    assert settings.background_task_max_attempts == 3


def test_database_engine_options_include_pool_controls():
    database_py = (ROOT / "fast_api" / "app" / "db" / "database.py").read_text(encoding="utf-8")

    assert "pool_pre_ping" in database_py
    assert "db_pool_size" in database_py
    assert "db_max_overflow" in database_py
    assert "db_pool_timeout_seconds" in database_py


def test_background_task_model_and_migration_exist():
    models = (ROOT / "fast_api" / "app" / "db" / "models.py").read_text(encoding="utf-8")
    migration = (ROOT / "alembic" / "versions" / "007_background_tasks.py").read_text(encoding="utf-8")

    assert "class BackgroundTask" in models
    assert '__tablename__ = "background_tasks"' in models
    assert "ix_background_tasks_status_created" in migration
    assert "006_decision_outcomes" in migration


def test_async_plan_endpoint_and_worker_are_wired():
    coach_api = (ROOT / "fast_api" / "app" / "api" / "coach_platform.py").read_text(encoding="utf-8")
    worker = (ROOT / "scripts" / "run_background_worker.py").read_text(encoding="utf-8")
    locustfile = (ROOT / "locustfile.py").read_text(encoding="utf-8")

    assert '"/plans/generate/async"' in coach_api
    assert '"/evals/run/async"' in coach_api
    assert '"/tasks/{task_id}"' in coach_api
    assert "run_one_background_task" in worker
    assert "enqueue_plan_generation" in locustfile
