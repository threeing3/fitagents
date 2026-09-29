from algorithm.evaluation.fitagent_postgres_proxy_restart_eval import (
    REQUIRED_CHECKS,
    _database_url,
    _psycopg_url,
    _runtime_environment,
    _utc_iso_now,
    report_passed,
)


def test_postgres_proxy_runtime_uses_declared_offline_database():
    database_url = "postgresql+psycopg://user@127.0.0.1:55432/fitagent_t13"
    environment = _runtime_environment(database_url)

    assert environment["DATABASE_URL"] == database_url
    assert environment["LLM_PROVIDER"] == "offline"
    assert environment["EMBEDDING_PROVIDER"] == "offline"
    assert environment["USE_PGVECTOR"] == "false"


def test_postgres_database_url_replaces_only_database_path():
    admin_url = "postgresql+psycopg://user@127.0.0.1:55432/postgres?application_name=test"

    assert _database_url(admin_url, "fitagent retained") == (
        "postgresql+psycopg://user@127.0.0.1:55432/fitagent%20retained?application_name=test"
    )
    assert _psycopg_url(admin_url).startswith("postgresql://")


def test_postgres_proxy_report_requires_every_frozen_check():
    passing = {"checks": {name: True for name in REQUIRED_CHECKS}}
    missing = {
        "checks": {
            name: True
            for name in REQUIRED_CHECKS
            if name != "proxy_received_complete_upstream_response"
        }
    }
    failing = {
        "checks": {
            name: name != "proxy_received_complete_upstream_response" for name in REQUIRED_CHECKS
        }
    }

    assert report_passed(passing) is True
    assert report_passed(missing) is False
    assert report_passed(failing) is False


def test_postgres_proxy_checks_freeze_bootstrap_evidence():
    assert "current_orm_schema_bootstrapped" in REQUIRED_CHECKS


def test_utc_experiment_timestamp_is_timezone_aware():
    assert _utc_iso_now().endswith("+00:00")
