"""Full app/worker on a dedicated synthetic PostgreSQL database, never existing jobs."""

import argparse
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["prepare", "serve", "worker", "worker-claim", "inspect", "migrate", "backup"],
    )
    parser.add_argument("--user-id")
    parser.add_argument("--receipt", default="logs/refactor_model_receipts_20261002.jsonl")
    args = parser.parse_args()
    database = "fitagent_acceptance_20261002"
    dsn = f"postgresql+psycopg://fitagent_test@127.0.0.1:15432/{database}"
    # Only this process changes settings. Existing .env and global settings stay intact.
    os.environ.update(
        {
            "DATABASE_URL": dsn,
            "USE_PGVECTOR": "false",
            "EMBEDDING_PROVIDER": "offline",
            "ENVIRONMENT": "development",
            "DEMO_MODE": "false",
            "INVITE_CODE": "",
            "JWT_SECRET_KEY": "synthetic-acceptance-only-key-not-for-real-accounts",
            "CODE_DRIVEN_PLANNER": "rule",
            "DAILY_MODEL_CALL_LIMIT": "8",
            "GLOBAL_DAILY_MODEL_LIMIT": "12",
            "LANGCHAIN_TRACING_V2": "false",
        }
    )
    if args.mode == "inspect":
        from sqlalchemy import inspect

        from fast_api.app.db import models
        from fast_api.app.db.database import engine

        inspector = inspect(engine)
        for table in models.Base.metadata.sorted_tables:
            columns = {item["name"]: item for item in inspector.get_columns(table.name)}
            missing = sorted(set(table.columns.keys()) - set(columns))
            extra_required = [
                key
                for key, value in columns.items()
                if key not in table.columns and not value["nullable"] and value["default"] is None
            ]
            if missing or extra_required:
                print(
                    {"table": table.name, "missing": missing, "extra_required": extra_required},
                    flush=True,
                )
                for key in missing:
                    column = table.columns[key]
                    print(
                        key,
                        str(column.type.compile(dialect=engine.dialect)),
                        "nullable=" + str(column.nullable),
                        "default=" + str(column.default.arg if column.default else None),
                        flush=True,
                    )
    elif args.mode == "backup":
        subprocess.run(
            [
                ".venv/Scripts/python.exe",
                "-m",
                "scripts.audit_and_backup_database",
                "--backup",
                "logs/backups/acceptance_pre_017_20261002.dump",
            ],
            check=True,
            timeout=120,
        )
    elif args.mode == "migrate":
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("alembic.ini"), "head")
    elif args.mode == "prepare":
        import psycopg
        from psycopg import sql

        with psycopg.connect(
            "host=127.0.0.1 port=15432 user=fitagent_test dbname=postgres", autocommit=True
        ) as connection:
            exists = connection.execute(
                "select 1 from pg_database where datname=%s", (database,)
            ).fetchone()
            if exists:
                raise ValueError(
                    "Acceptance database already exists; refusing to overwrite or repeat preparation"
                )
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        from alembic import command
        from alembic.config import Config

        configuration = Config("alembic.ini")
        command.upgrade(configuration, "014_idempotency_records")
        backup = Path("logs/backups/acceptance_pre_015_20261002.dump")
        subprocess.run(
            [
                ".venv/Scripts/python.exe",
                "-m",
                "scripts.audit_and_backup_database",
                "--backup",
                str(backup),
            ],
            check=True,
            timeout=120,
        )
        command.upgrade(configuration, "015_pending_approvals")
        print(
            "PASS: isolated 014 database backed up, archive listing verified, then migrated to 015",
            flush=True,
        )
    elif args.mode == "serve":
        import json
        import traceback
        from contextlib import asynccontextmanager

        import uvicorn

        from fast_api.app.main import app
        from fast_api.app.services.model_provider import ModelProvider

        original_reply = ModelProvider.coach_reply

        async def audited_reply(provider, system_prompt, user_prompt):
            # Audit the real provider return, never replace/mimic the model.
            receipt = {
                "provider": provider.settings.llm_provider,
                "model": provider.settings.chat_model,
                "user_id": str(provider.user_id),
            }
            try:
                result = await original_reply(provider, system_prompt, user_prompt)
                receipt["status"] = "live_return" if result else "no_live_return"
                receipt["response_chars"] = len(result or "")
                return result
            except Exception as exc:
                receipt["status"] = "failed"
                receipt["error_type"] = type(exc).__name__
                raise
            finally:
                with Path(args.receipt).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(receipt) + "\n")

        ModelProvider.coach_reply = audited_reply

        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def visible_lifespan(application):
            try:
                async with original_lifespan(application):
                    yield
            except Exception:
                traceback.print_exc()
                raise

        app.router.lifespan_context = visible_lifespan

        uvicorn.run(app, host="127.0.0.1", port=1016)
    else:
        import uuid

        from fast_api.app.db.database import SessionLocal
        from fast_api.app.services.background_tasks import (
            BackgroundTaskQueue,
            run_one_background_task,
        )

        if not args.user_id:
            raise ValueError("Worker must be scoped to the synthetic acceptance user")
        with SessionLocal() as db:
            task = (
                BackgroundTaskQueue(db).claim_next(user_id=uuid.UUID(args.user_id))
                if args.mode == "worker-claim"
                else run_one_background_task(db, user_id=uuid.UUID(args.user_id))
            )
            print(
                {"processed": bool(task), "task_status": task.status if task else None}, flush=True
            )


if __name__ == "__main__":
    main()
