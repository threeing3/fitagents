"""Run the existing isolated local preview without migrations or secret prompts."""

import argparse
import os
from pathlib import Path

from scripts.run_local_byok import LocalModelAccess, build_environment

ROOT = Path(__file__).resolve().parents[1]
DSN = "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"


def configure_local(*, offline: bool = False) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from fast_api.app.core.config import get_settings

    original = get_settings()
    access = (
        LocalModelAccess("offline")
        if offline
        else LocalModelAccess(
            original.llm_provider, original.chat_model, original.chat_api_key or ""
        )
    )
    os.environ.update(build_environment(access, DSN, 8015))
    os.environ["STREAM_JOURNAL_BACKEND"] = "file"
    get_settings.cache_clear()


def check_database() -> None:
    from sqlalchemy import inspect, text

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from fast_api.app.db.database import engine
    from fast_api.app.db.models import Base

    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version")).scalar_one()
        file_compatible = version == "017_domain_jsonb_alignment" and head == "018_stream_journals"
        if version != head and not file_compatible:
            raise RuntimeError(
                "Database version mismatch. Audit/backup before migrating; no changes made."
            )
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            if file_compatible and table.name in {"stream_journals", "stream_journal_events"}:
                continue
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Missing table: {table.name}; no changes made.")
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            if set(table.columns.keys()) - actual:
                raise RuntimeError(f"Missing columns: {table.name}; no changes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT or not (ROOT / "web/dist/index.html").is_file():
        raise SystemExit("Run from the repository root and build web first.")
    configure_local(offline=args.offline)
    check_database()
    print("Database schema verified; no migrations or seeding performed.", flush=True)
    if args.check_only:
        return
    print("Local preview: http://127.0.0.1:8015/ ; background worker is disabled.", flush=True)
    print("Live mode uses existing local credentials and can incur charges.", flush=True)
    import uvicorn

    uvicorn.run("fast_api.app.main:app", host="127.0.0.1", port=8015, lifespan="off")


if __name__ == "__main__":
    main()
