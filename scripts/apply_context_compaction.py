"""Back up the configured local database, then apply only approved migration 019."""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from fast_api.app.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-reviewed-history", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Only the authorized local database may be migrated")
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        revision = connection.scalar(text("select version_num from alembic_version"))
        if revision == "019_context_compaction":
            print("Migration 019 already applied; no changes made", flush=True)
            return
        allowed = {"018_stream_journals"}
        if args.include_reviewed_history:
            allowed |= {"011_chat_message_token_counts", "017_domain_jsonb_alignment"}
        if revision not in allowed:
            raise RuntimeError("Unexpected database revision; refusing broader migrations")
        before = connection.scalar(text("select count(*) from chat_messages"))
        tables = [
            name for name in inspect(connection).get_table_names() if name != "alembic_version"
        ]
        counts = {
            name: connection.scalar(text(f'SELECT count(*) FROM "{name}"')) for name in tables
        }
        chat_rows = connection.execute(
            text("select id,user_id,session_id,role,content from chat_messages order by id")
        ).all()
    backup = (
        root
        / "output"
        / "db-backups"
        / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_context_compaction.dump")
    )
    subprocess.run(
        [sys.executable, "-m", "scripts.audit_and_backup_database", "--backup", str(backup)],
        cwd=root,
        check=True,
        timeout=150,
    )
    clone_name = "fitagent_compaction_verify_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{clone_name}"'))
    admin.dispose()
    environment = os.environ.copy()
    environment["PGPASSWORD"] = url.password or ""
    restored = subprocess.run(
        [
            "E:/PostgreSQL/bin/pg_restore.exe",
            "-h",
            url.host,
            "-p",
            str(url.port or 5432),
            "-U",
            url.username,
            "-d",
            clone_name,
            "--no-owner",
            "--no-privileges",
            str(backup),
        ],
        env=environment,
        check=False,
        timeout=120,
        capture_output=True,
        text=True,
    )
    if restored.returncode:
        # pg_restore 18 emits this setting even when restoring to PostgreSQL 16.
        # Only that single non-data setting error is allowed; all others stop.
        error = restored.stderr
        if not (
            'unrecognized configuration parameter "transaction_timeout"' in error
            and error.count("pg_restore: error:") == 1
            and "errors ignored on restore: 1" in error
        ):
            raise RuntimeError("Clone restore failed; original database was not migrated")

    def upgrade(target):
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = target
        get_settings.cache_clear()
        try:
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "alembic"))
            config.attributes["context_compaction_backup_verified"] = True
            command.upgrade(config, "019_context_compaction")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()

    def verify(connection):
        for name, count in counts.items():
            if connection.scalar(text(f'SELECT count(*) FROM "{name}"')) != count:
                raise RuntimeError("Original table row count changed: " + name)
        if (
            connection.execute(
                text("select id,user_id,session_id,role,content from chat_messages order by id")
            ).all()
            != chat_rows
        ):
            raise RuntimeError("Original chat content changed")

    clone_url = url.set(database=clone_name).render_as_string(hide_password=False)
    upgrade(clone_url)
    clone = create_engine(clone_url)
    with clone.connect() as connection:
        verify(connection)
    clone.dispose()
    print(
        {"clone_verified": clone_name, "existing_table_counts_preserved": len(counts)}, flush=True
    )
    upgrade(settings.database_url)
    with engine.connect() as connection:
        revision = connection.scalar(text("select version_num from alembic_version"))
        after = connection.scalar(text("select count(*) from chat_messages"))
        verify(connection)
    if revision != "019_context_compaction" or before != after:
        raise RuntimeError("Post-migration verification requires inspection")
    print({"revision": revision, "chat_rows_preserved": after, "backup": str(backup)}, flush=True)
    engine.dispose()


if __name__ == "__main__":
    main()
