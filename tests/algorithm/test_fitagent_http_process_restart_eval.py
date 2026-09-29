from algorithm.evaluation.fitagent_http_process_restart_eval import (
    REQUIRED_CHECKS,
    _runtime_environment,
    report_passed,
)


def test_process_restart_runtime_uses_declared_offline_sqlite(tmp_path):
    database_path = tmp_path / "restart.sqlite3"
    environment = _runtime_environment(database_path)

    assert environment["DATABASE_URL"].startswith("sqlite:///")
    assert environment["DATABASE_URL"].endswith("restart.sqlite3")
    assert environment["LLM_PROVIDER"] == "offline"
    assert environment["EMBEDDING_PROVIDER"] == "offline"
    assert environment["USE_PGVECTOR"] == "false"


def test_process_restart_report_requires_every_frozen_check():
    passing = {"checks": {name: True for name in REQUIRED_CHECKS}}
    missing = {
        "checks": {name: True for name in REQUIRED_CHECKS if name != "same_checkin_returned"}
    }
    failing = {"checks": {name: name != "same_checkin_returned" for name in REQUIRED_CHECKS}}

    assert report_passed(passing) is True
    assert report_passed(missing) is False
    assert report_passed(failing) is False
