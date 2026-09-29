"""Verify PostgreSQL replay after an independent proxy drops a completed response."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from algorithm.evaluation.fitagent_http_process_restart_eval import (
    PROJECT_ROOT,
    RunningServer,
    _find_adjustment_evaluation,
    _find_strategy_followup,
    _free_port,
    _request_json,
    _stop_server,
    _wait_until_ready,
)

REQUIRED_CHECKS = {
    "postgres_backend_confirmed",
    "current_orm_schema_bootstrapped",
    "proxy_received_complete_upstream_response",
    "proxy_forwarded_no_response_bytes",
    "client_observed_lost_response",
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
    "all_server_processes_stopped",
    "fault_proxy_stopped",
}


@dataclass
class ProxyObservation:
    request_bytes: int = 0
    upstream_response_bytes: int = 0
    upstream_status_line: str = ""
    downstream_response_bytes: int = 0
    error: str | None = None


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _psycopg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_url(admin_url: str, database_name: str) -> str:
    parsed = urlsplit(admin_url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{quote(database_name, safe='')}",
            parsed.query,
            parsed.fragment,
        )
    )


def _runtime_environment(database_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "USE_PGVECTOR": "false",
            "LLM_PROVIDER": "offline",
            "EMBEDDING_PROVIDER": "offline",
            "JWT_SECRET_KEY": "postgres-proxy-restart-evaluation-secret-20260922",
            "JWT_ALGORITHM": "HS256",
            "JWT_EXPIRE_MINUTES": "60",
            "CORS_ORIGINS": "http://127.0.0.1",
            "DEMO_MODE": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _create_retained_database(admin_url: str, database_name: str) -> dict[str, str]:
    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
        ).fetchone()
        if exists:
            raise RuntimeError(f"Retained experiment database already exists: {database_name}")
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    database_url = _database_url(admin_url, database_name)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        version = str(connection.execute("SELECT version()").fetchone()[0])
        current_database = str(connection.execute("SELECT current_database()").fetchone()[0])
    return {
        "database_url": database_url,
        "current_database": current_database,
        "server_family": version.split(",", maxsplit=1)[0],
    }


def _bootstrap_current_orm_schema(run_dir: Path, database_url: str) -> dict[str, Any]:
    bootstrap_log = run_dir / "bootstrap_current_orm_schema.log"
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
    with bootstrap_log.open("w", encoding="utf-8") as log_handle:
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=_runtime_environment(database_url),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        table_count = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()[0]
    return {
        "mode": "current_orm_create_all_then_stamp",
        "alembic_version": str(version),
        "public_table_count": int(table_count),
    }


def _start_server(
    run_dir: Path, database_url: str, phase: str
) -> tuple[RunningServer, dict[str, Any]]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_handle = (run_dir / f"server_{phase}.log").open("w", encoding="utf-8")
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
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=_runtime_environment(database_url),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    server = RunningServer(process=process, log_handle=log_handle, base_url=base_url, phase=phase)
    try:
        readiness = _wait_until_ready(base_url)
    except Exception:
        _stop_server(server)
        raise
    return server, readiness


def _receive_http_request(connection: socket.socket) -> bytes:
    payload = bytearray()
    content_length = 0
    header_end = -1
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        payload.extend(chunk)
        if header_end < 0:
            header_end = payload.find(b"\r\n\r\n")
            if header_end >= 0:
                headers = payload[:header_end].decode("iso-8859-1")
                for line in headers.split("\r\n")[1:]:
                    name, _, value = line.partition(":")
                    if name.lower() == "content-length":
                        content_length = int(value.strip())
                        break
        if header_end >= 0 and len(payload) >= header_end + 4 + content_length:
            break
    return bytes(payload)


def _run_one_shot_drop_proxy(
    listen_socket: socket.socket,
    upstream_port: int,
    observation: ProxyObservation,
    finished: threading.Event,
) -> None:
    try:
        client, _ = listen_socket.accept()
        with client:
            client.settimeout(20)
            request = _receive_http_request(client)
            observation.request_bytes = len(request)
            with socket.create_connection(("127.0.0.1", upstream_port), timeout=20) as upstream:
                upstream.settimeout(20)
                upstream.sendall(request)
                response = bytearray()
                while True:
                    chunk = upstream.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
            observation.upstream_response_bytes = len(response)
            if response:
                observation.upstream_status_line = response.split(b"\r\n", maxsplit=1)[0].decode(
                    "ascii", errors="replace"
                )
            # Deliberately forward zero response bytes and close the client connection.
    except Exception as exc:  # pragma: no cover - captured in experiment report
        observation.error = f"{type(exc).__name__}: {exc}"
    finally:
        listen_socket.close()
        finished.set()


def _drop_completed_response(
    upstream_base_url: str,
    path: str,
    payload: dict[str, Any],
    token: str,
    idempotency_key: str,
) -> tuple[ProxyObservation, str]:
    upstream = urlsplit(upstream_base_url)
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(("127.0.0.1", 0))
    listen_socket.listen(1)
    proxy_port = int(listen_socket.getsockname()[1])
    observation = ProxyObservation()
    finished = threading.Event()
    thread = threading.Thread(
        target=_run_one_shot_drop_proxy,
        args=(listen_socket, int(upstream.port or 80), observation, finished),
        name="fitagent-one-shot-drop-proxy",
        daemon=False,
    )
    thread.start()
    client_error = ""
    try:
        _request_json(
            f"http://127.0.0.1:{proxy_port}",
            path,
            method="POST",
            payload=payload,
            token=token,
            headers={"Idempotency-Key": idempotency_key, "Connection": "close"},
            timeout=25,
        )
    except Exception as exc:
        client_error = f"{type(exc).__name__}: {exc}"
    if not finished.wait(timeout=25):
        listen_socket.close()
        raise RuntimeError("Fault proxy did not finish within 25 seconds")
    thread.join(timeout=2)
    if thread.is_alive():
        raise RuntimeError("Fault proxy thread remained alive")
    return observation, client_error


def _database_counts(
    database_url: str, email: str, first_date: str, idempotency_key: str
) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        user_row = connection.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if user_row is None:
            raise RuntimeError("Synthetic PostgreSQL proxy user was not persisted")
        user_id = user_row[0]

        def count(query: str, params: tuple[Any, ...]) -> int:
            return int(connection.execute(query, params).fetchone()[0])

        evaluation_row = connection.execute(
            "SELECT status, outcome_status FROM decision_evaluation_plans "
            "WHERE user_id = %s AND evaluation_type = 'training_adjustment'",
            (user_id,),
        ).fetchone()
        return {
            "initial_checkins": count(
                "SELECT COUNT(*) FROM daily_checkins WHERE user_id = %s AND checkin_date = %s",
                (user_id, first_date),
            ),
            "initial_idempotency_records": count(
                "SELECT COUNT(*) FROM idempotency_records "
                "WHERE user_id = %s AND operation = 'daily_checkin' AND idempotency_key = %s",
                (user_id, idempotency_key),
            ),
            "adjustment_decisions": count(
                "SELECT COUNT(*) FROM agent_decisions "
                "WHERE user_id = %s AND decision_type = 'plan_adjustment'",
                (user_id,),
            ),
            "adjustment_evaluations": count(
                "SELECT COUNT(*) FROM decision_evaluation_plans "
                "WHERE user_id = %s AND evaluation_type = 'training_adjustment'",
                (user_id,),
            ),
            "decision_outcomes": count(
                "SELECT COUNT(*) FROM decision_outcomes WHERE user_id = %s", (user_id,)
            ),
            "experience_memories": count(
                "SELECT COUNT(*) FROM long_term_memories "
                "WHERE user_id = %s AND memory_network = 'experience' "
                "AND source = 'decision_outcome'",
                (user_id,),
            ),
            "evaluation_status": evaluation_row[0] if evaluation_row else None,
            "evaluation_outcome_status": evaluation_row[1] if evaluation_row else None,
        }


def report_passed(report: dict[str, Any]) -> bool:
    checks = report.get("checks") or {}
    return set(checks) == REQUIRED_CHECKS and all(checks.values())


def run_experiment(run_dir: Path, admin_url: str) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    database_name = f"fitagent_t13_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    database = _create_retained_database(admin_url, database_name)
    database_url = database.pop("database_url")
    bootstrap = _bootstrap_current_orm_schema(run_dir, database_url)
    email = f"postgres-proxy-{datetime.now().strftime('%Y%m%d%H%M%S')}@example.com"
    password = "synthetic-pass-20260922"
    idempotency_key = "task-state-13-proxy-dropped-checkin"
    first_date = date.today().isoformat()
    phases: list[dict[str, Any]] = []
    servers: list[RunningServer] = []

    phase1, ready1 = _start_server(run_dir, database_url, "phase1")
    servers.append(phase1)
    try:
        _, registration = _request_json(
            phase1.base_url,
            "/v1/auth/register",
            method="POST",
            payload={"email": email, "password": password, "display_name": "Synthetic PG Proxy"},
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
            "notes": "Synthetic response dropped by independent proxy.",
        }
        proxy_observation, client_error = _drop_completed_response(
            phase1.base_url,
            "/v1/checkins/daily",
            dropped_payload,
            token,
            idempotency_key,
        )
        phases.append({"phase": "phase1", "pid": phase1.process.pid, "ready": ready1})
    finally:
        phase1_exit = _stop_server(phase1)

    phase2, ready2 = _start_server(run_dir, database_url, "phase2")
    servers.append(phase2)
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
            phase2.base_url, "/v1/agent/decision-evaluations", token=token
        )
        adjustment = _find_adjustment_evaluation(evaluations_before)
        _request_json(
            phase2.base_url,
            "/v1/checkins/daily",
            method="POST",
            token=token,
            headers={"Idempotency-Key": "task-state-13-followup-checkin"},
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
            headers={"Idempotency-Key": "task-state-13-workout"},
            payload={
                "performed_at": _utc_iso_now(),
                "workout_name": "Synthetic PostgreSQL reduced-load workout",
                "duration_minutes": 35,
                "rpe": 5,
                "completion_rate": 0.9,
                "exercises": [{"name": "dumbbell row", "sets": [{"reps": 10, "weight": 12}]}],
            },
        )
        _, followups = _request_json(phase2.base_url, "/v1/agent/decision-followups", token=token)
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
            phase2.base_url, "/v1/agent/decision-evaluations", token=token
        )
        completed_phase2 = _find_adjustment_evaluation(evaluations_after)
        phases.append({"phase": "phase2", "pid": phase2.process.pid, "ready": ready2})
    finally:
        phase2_exit = _stop_server(phase2)

    phase3, ready3 = _start_server(run_dir, database_url, "phase3")
    servers.append(phase3)
    try:
        _, me = _request_json(phase3.base_url, "/v1/auth/me", token=token)
        _, evaluations_phase3 = _request_json(
            phase3.base_url, "/v1/agent/decision-evaluations", token=token
        )
        completed_phase3 = _find_adjustment_evaluation(evaluations_phase3)
        phases.append({"phase": "phase3", "pid": phase3.process.pid, "ready": ready3})
    finally:
        phase3_exit = _stop_server(phase3)

    counts = _database_counts(database_url, email, first_date, idempotency_key)
    checks = {
        "postgres_backend_confirmed": database["current_database"] == database_name,
        "current_orm_schema_bootstrapped": (
            bootstrap["alembic_version"] == "014_idempotency_records"
            and bootstrap["public_table_count"] > 0
        ),
        "proxy_received_complete_upstream_response": (
            proxy_observation.upstream_response_bytes > 0
            and proxy_observation.upstream_status_line.startswith("HTTP/1.1 200")
            and proxy_observation.error is None
        ),
        "proxy_forwarded_no_response_bytes": proxy_observation.downstream_response_bytes == 0,
        "client_observed_lost_response": bool(client_error),
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
        "all_server_processes_stopped": all(
            server.process.poll() is not None for server in servers
        ),
        "fault_proxy_stopped": not any(
            thread.name == "fitagent-one-shot-drop-proxy" and thread.is_alive()
            for thread in threading.enumerate()
        ),
    }
    report = {
        "schema_version": "fitagent-postgres-proxy-restart/v1",
        "run_dir": str(run_dir),
        "synthetic_data_only": True,
        "training_eligible": False,
        "database": {
            "name": database_name,
            "server_family": database["server_family"],
            "retained": True,
            "bootstrap": bootstrap,
        },
        "phases": phases,
        "server_exit_codes": [phase1_exit, phase2_exit, phase3_exit],
        "proxy_observation": {
            "request_bytes": proxy_observation.request_bytes,
            "upstream_response_bytes": proxy_observation.upstream_response_bytes,
            "upstream_status_line": proxy_observation.upstream_status_line,
            "downstream_response_bytes": proxy_observation.downstream_response_bytes,
            "error": proxy_observation.error,
            "client_error_type": client_error.split(":", maxsplit=1)[0] if client_error else None,
        },
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
            "PostgreSQL, proxy, and all API processes run on one Windows host.",
            "The retained database uses current ORM create_all plus an Alembic head stamp because "
            "fresh migration parity is tracked as a separate blocker.",
            "The proxy drops a completed response; it does not kill a transaction mid-commit.",
            "The experiment uses one synthetic user and controlled sequential process restarts.",
            "This demonstrates durable replay under the frozen fault, not production exactly-once.",
        ],
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--admin-url", required=True)
    args = parser.parse_args()
    report = run_experiment(args.run_dir, args.admin_url)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, indent=2))
    raise SystemExit(0 if report_passed(report) else 1)


if __name__ == "__main__":
    main()
