"""Run a real HTTP process-restart and lost-response recovery experiment."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_PARENT = PROJECT_ROOT / "logs" / "experiments"
REQUIRED_CHECKS = {
    "first_response_intentionally_unobserved",
    "request_replayed_after_process_restart",
    "same_checkin_returned",
    "single_initial_checkin_write",
    "single_initial_idempotency_record",
    "single_adjustment_decision",
    "single_adjustment_evaluation",
    "evaluation_completed_before_second_restart",
    "evaluation_survived_third_process",
    "single_decision_outcome",
    "single_experience_memory",
}


@dataclass
class RunningServer:
    process: subprocess.Popen[str]
    log_handle: Any
    base_url: str
    phase: str


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime_environment(database_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": f"sqlite:///{database_path.resolve().as_posix()}",
            "USE_PGVECTOR": "false",
            "LLM_PROVIDER": "offline",
            "EMBEDDING_PROVIDER": "offline",
            "JWT_SECRET_KEY": "process-restart-evaluation-secret-key-20260922",
            "JWT_ALGORITHM": "HS256",
            "JWT_EXPIRE_MINUTES": "60",
            "CORS_ORIGINS": "http://127.0.0.1",
            "DEMO_MODE": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _bootstrap_sqlite_database(run_dir: Path, database_path: Path) -> None:
    bootstrap_log = run_dir / "bootstrap.log"
    script = """
from sqlalchemy import text
from fast_api.app.db import models  # noqa: F401
from fast_api.app.db.database import Base, engine

Base.metadata.create_all(bind=engine)
with engine.begin() as connection:
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL)"
    ))
    connection.execute(text(
        "INSERT INTO alembic_version (version_num) "
        "SELECT '014_idempotency_records' "
        "WHERE NOT EXISTS (SELECT 1 FROM alembic_version)"
    ))
print("created current ORM schema and stamped 014_idempotency_records")
"""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with bootstrap_log.open("w", encoding="utf-8") as log_handle:
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=_runtime_environment(database_path),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
            creationflags=creation_flags,
        )


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, Any]:
    body = None
    request_headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else None
            return int(response.status), parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {path}: {raw}") from exc


def _wait_until_ready(base_url: str, timeout_seconds: float = 45) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = _request_json(base_url, "/health/ready", timeout=2)
            if status == 200 and payload.get("status") == "ready":
                return payload
        except Exception as exc:  # pragma: no cover - timing depends on process startup
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Server did not become ready: {last_error}")


def _start_server(
    run_dir: Path,
    database_path: Path,
    phase: str,
) -> tuple[RunningServer, dict[str, Any]]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = run_dir / f"server_{phase}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "fast_api.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "info",
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=_runtime_environment(database_path),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creation_flags,
    )
    server = RunningServer(
        process=process,
        log_handle=log_handle,
        base_url=base_url,
        phase=phase,
    )
    try:
        readiness = _wait_until_ready(base_url)
    except Exception:
        _stop_server(server)
        raise
    return server, readiness


def _stop_server(server: RunningServer) -> int:
    if server.process.poll() is None:
        server.process.terminate()
        try:
            server.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.process.kill()
            server.process.wait(timeout=10)
    exit_code = int(server.process.returncode or 0)
    server.log_handle.close()
    return exit_code


def _send_json_without_reading_response(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    token: str,
    idempotency_key: str,
) -> None:
    parsed = urlsplit(base_url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        f"POST {path} HTTP/1.1",
        f"Host: {parsed.hostname}:{parsed.port}",
        "Content-Type: application/json",
        "Accept: application/json",
        f"Authorization: Bearer {token}",
        f"Idempotency-Key: {idempotency_key}",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "",
        "",
    ]
    with socket.create_connection(
        (str(parsed.hostname), int(parsed.port or 80)), timeout=10
    ) as sock:
        sock.sendall("\r\n".join(headers).encode("ascii") + body)
        time.sleep(0.15)
        # Intentionally close without reading status, headers, or body.


def _find_adjustment_evaluation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [row for row in rows if row.get("evaluation_type") == "training_adjustment"]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one adjustment evaluation, found {len(matches)}")
    return matches[0]


def _find_strategy_followup(
    rows: list[dict[str, Any]],
    evaluation_id: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in rows
        if row.get("evaluation_plan_id") == evaluation_id
        and row.get("question_type") == "strategy_execution"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one strategy follow-up, found {len(matches)}")
    return matches[0]


def _database_counts(database_path: Path, email: str, first_date: str) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        user_row = connection.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if user_row is None:
            raise RuntimeError("Synthetic process-restart user was not persisted")
        user_id = user_row[0]

        def count(table: str, clause: str = "1=1", params: tuple[Any, ...] = ()) -> int:
            query = f"SELECT COUNT(*) FROM {table} WHERE {clause}"  # noqa: S608
            return int(connection.execute(query, params).fetchone()[0])

        evaluation_row = connection.execute(
            "SELECT status, outcome_status FROM decision_evaluation_plans "
            "WHERE user_id = ? AND evaluation_type = 'training_adjustment'",
            (user_id,),
        ).fetchone()
        return {
            "initial_checkins": count(
                "daily_checkins",
                "user_id = ? AND checkin_date = ?",
                (user_id, first_date),
            ),
            "initial_idempotency_records": count(
                "idempotency_records",
                "user_id = ? AND operation = 'daily_checkin' "
                "AND idempotency_key = 'task-state-12-dropped-checkin'",
                (user_id,),
            ),
            "adjustment_decisions": count(
                "agent_decisions",
                "user_id = ? AND decision_type = 'plan_adjustment'",
                (user_id,),
            ),
            "adjustment_evaluations": count(
                "decision_evaluation_plans",
                "user_id = ? AND evaluation_type = 'training_adjustment'",
                (user_id,),
            ),
            "decision_outcomes": count("decision_outcomes", "user_id = ?", (user_id,)),
            "experience_memories": count(
                "long_term_memories",
                "user_id = ? AND memory_network = 'experience' AND source = 'decision_outcome'",
                (user_id,),
            ),
            "evaluation_status": evaluation_row[0] if evaluation_row else None,
            "evaluation_outcome_status": evaluation_row[1] if evaluation_row else None,
        }


def report_passed(report: dict[str, Any]) -> bool:
    checks = report.get("checks") or {}
    return set(checks) == REQUIRED_CHECKS and all(checks.values())


def run_experiment(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    database_path = run_dir / "fitagent_process_restart.sqlite3"
    _bootstrap_sqlite_database(run_dir, database_path)
    email = "process-restart-20260922@example.com"
    password = "synthetic-pass-20260922"
    idempotency_key = "task-state-12-dropped-checkin"
    first_date = date.today().isoformat()
    phases: list[dict[str, Any]] = []

    phase1, ready1 = _start_server(run_dir, database_path, "phase1")
    try:
        _, registration = _request_json(
            phase1.base_url,
            "/v1/auth/register",
            method="POST",
            payload={
                "email": email,
                "password": password,
                "display_name": "Synthetic Process Restart",
            },
        )
        token = str(registration["access_token"])
        user_id = str(registration["user_id"])
        _request_json(
            phase1.base_url,
            "/v1/profiles",
            method="POST",
            token=token,
            payload={
                "age": 25,
                "sex": "male",
                "height_cm": 175,
                "weight_kg": 70,
                "goal": "muscle_gain",
                "experience_level": "beginner",
                "workout_frequency": 3,
                "equipment_available": ["dumbbells"],
                "injuries": [],
            },
        )
        _request_json(
            phase1.base_url,
            "/v1/plans/generate",
            method="POST",
            token=token,
            payload={"force": True, "plan_days": 7},
        )
        dropped_payload = {
            "checkin_date": first_date,
            "sleep_hours": 5.0,
            "fatigue": 9,
            "soreness": 6,
            "workout_completion": 60,
            "notes": "Synthetic request whose HTTP response is intentionally unread.",
        }
        _send_json_without_reading_response(
            phase1.base_url,
            "/v1/checkins/daily",
            dropped_payload,
            token,
            idempotency_key,
        )
        time.sleep(1.5)
        phases.append({"phase": "phase1", "pid": phase1.process.pid, "ready": ready1})
    finally:
        phase1_exit = _stop_server(phase1)

    phase2, ready2 = _start_server(run_dir, database_path, "phase2")
    try:
        _, replay = _request_json(
            phase2.base_url,
            "/v1/checkins/daily",
            method="POST",
            token=token,
            headers={"Idempotency-Key": idempotency_key},
            payload=dropped_payload,
        )
        _, evaluations_before = _request_json(
            phase2.base_url,
            "/v1/agent/decision-evaluations",
            token=token,
        )
        adjustment = _find_adjustment_evaluation(evaluations_before)
        _request_json(
            phase2.base_url,
            "/v1/checkins/daily",
            method="POST",
            token=token,
            headers={"Idempotency-Key": "task-state-12-followup-checkin"},
            payload={
                "checkin_date": (date.today() + timedelta(days=1)).isoformat(),
                "sleep_hours": 7.5,
                "fatigue": 4,
                "soreness": 2,
                "workout_completion": 90,
            },
        )
        _request_json(
            phase2.base_url,
            "/v1/workouts/logs",
            method="POST",
            token=token,
            headers={"Idempotency-Key": "task-state-12-workout"},
            payload={
                "performed_at": datetime.utcnow().isoformat(),
                "workout_name": "Synthetic reduced-load workout",
                "duration_minutes": 35,
                "rpe": 5,
                "completion_rate": 0.9,
                "exercises": [{"name": "dumbbell row", "sets": [{"reps": 10, "weight": 12}]}],
            },
        )
        _, followups = _request_json(
            phase2.base_url,
            "/v1/agent/decision-followups",
            token=token,
        )
        followup = _find_strategy_followup(followups, str(adjustment["id"]))
        _request_json(
            phase2.base_url,
            f"/v1/agent/decision-followups/{followup['id']}/answer",
            method="POST",
            token=token,
            payload={
                "implementation_status": "implemented",
                "subjective_outcome": "improved",
                "comment": "Synthetic recovery improved after reduced load.",
            },
        )
        _, evaluations_after = _request_json(
            phase2.base_url,
            "/v1/agent/decision-evaluations",
            token=token,
        )
        completed_phase2 = _find_adjustment_evaluation(evaluations_after)
        phases.append({"phase": "phase2", "pid": phase2.process.pid, "ready": ready2})
    finally:
        phase2_exit = _stop_server(phase2)

    phase3, ready3 = _start_server(run_dir, database_path, "phase3")
    try:
        _, me = _request_json(phase3.base_url, "/v1/auth/me", token=token)
        _, evaluations_phase3 = _request_json(
            phase3.base_url,
            "/v1/agent/decision-evaluations",
            token=token,
        )
        completed_phase3 = _find_adjustment_evaluation(evaluations_phase3)
        phases.append({"phase": "phase3", "pid": phase3.process.pid, "ready": ready3})
    finally:
        phase3_exit = _stop_server(phase3)

    counts = _database_counts(database_path, email, first_date)
    checks = {
        "first_response_intentionally_unobserved": True,
        "request_replayed_after_process_restart": replay.get("idempotent_replay") is True,
        "same_checkin_returned": bool(replay.get("checkin_id")),
        "single_initial_checkin_write": counts["initial_checkins"] == 1,
        "single_initial_idempotency_record": counts["initial_idempotency_records"] == 1,
        "single_adjustment_decision": counts["adjustment_decisions"] == 1,
        "single_adjustment_evaluation": counts["adjustment_evaluations"] == 1,
        "evaluation_completed_before_second_restart": (
            completed_phase2.get("status") == "completed"
            and completed_phase2.get("outcome_status") == "improved"
        ),
        "evaluation_survived_third_process": (
            completed_phase3.get("id") == completed_phase2.get("id")
            and completed_phase3.get("status") == "completed"
            and me.get("user_id") == user_id
        ),
        "single_decision_outcome": counts["decision_outcomes"] == 1,
        "single_experience_memory": counts["experience_memories"] == 1,
    }
    report = {
        "schema_version": "fitagent-http-process-restart/v1",
        "run_dir": str(run_dir),
        "database_path": str(database_path),
        "synthetic_data_only": True,
        "training_eligible": False,
        "phases": phases,
        "server_exit_codes": [phase1_exit, phase2_exit, phase3_exit],
        "checks": checks,
        "passed": all(checks.values()),
        "database_counts": counts,
        "observed": {
            "replay_checkin_id": replay.get("checkin_id"),
            "replay_flag": replay.get("idempotent_replay"),
            "adjustment_evaluation_id": completed_phase3.get("id"),
            "final_evaluation_status": completed_phase3.get("status"),
            "final_outcome_status": completed_phase3.get("outcome_status"),
        },
        "limitations": [
            "The database is a retained local SQLite file, not PostgreSQL.",
            "The client closes without reading the first response; no external network proxy is used.",
            "Each server is a real separate process, but all run on one Windows host.",
            "The experiment demonstrates durable replay and state recovery, not production exactly-once.",
        ],
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_experiment(args.run_dir)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, indent=2))
    raise SystemExit(0 if report_passed(report) else 1)


if __name__ == "__main__":
    main()
