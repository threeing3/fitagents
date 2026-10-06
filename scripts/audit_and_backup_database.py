"""Audit configured DB, optional local pg_dump backup. Never logs credentials."""

import argparse
import os
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from fast_api.app.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    url = make_url(settings.database_url)
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            revisions = (
                connection.execute(text("select version_num from alembic_version")).scalars().all()
                if "alembic_version" in tables
                else []
            )
            print(
                {
                    "database": url.database,
                    "revision": revisions,
                    "approval_table_exists": "pending_approvals" in tables,
                },
                flush=True,
            )
            if "background_tasks" in tables:
                print(
                    {
                        "task_counts": connection.execute(
                            text("select status,count(*) from background_tasks group by status")
                        ).all()
                    },
                    flush=True,
                )
        if args.backup:
            target = args.backup.resolve()
            if target.exists():
                raise ValueError("Backup target already exists; never overwrite")
            target.parent.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update({"PGPASSWORD": url.password or "", "PGCONNECT_TIMEOUT": "5"})
            result = subprocess.run(
                [
                    "E:/PostgreSQL/bin/pg_dump.exe",
                    "-h",
                    url.host or "127.0.0.1",
                    "-p",
                    str(url.port or 5432),
                    "-U",
                    url.username or "",
                    "-d",
                    url.database or "",
                    "--format=custom",
                    "--file",
                    str(target),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError("pg_dump failed; backup is not verified")
            verify = subprocess.run(
                ["E:/PostgreSQL/bin/pg_restore.exe", "--list", str(target)],
                capture_output=True,
                timeout=30,
            )
            if verify.returncode or target.stat().st_size == 0:
                raise RuntimeError("Backup archive verification failed")
            print(
                {
                    "backup": str(target),
                    "bytes": target.stat().st_size,
                    "archive_listing_verified": True,
                },
                flush=True,
            )
    except Exception as exc:
        print(
            {"status": "failed", "error_type": type(exc).__name__, "credentials_not_logged": True},
            flush=True,
        )
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
