"""Kill only the owned test child; inspect actual approval/plan commits afterward."""

import os
import queue
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.background_tasks import run_one_background_task
from fast_api.app.services.execution_trace import recorded_execution_trace
from fast_api.app.services.stream_event_pages import stream_event_page
from fast_api.app.services.write_receipts import approved_plan_write_receipt


def verify_real_http_restart(directory, owner_id, other_id, run_id, committed, database_url):
    worker = Path(__file__).parent / "fixtures" / "trace_http_worker.py"
    process = subprocess.Popen(
        [sys.executable, "-u", str(worker), str(directory), database_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    ready = queue.Queue()
    thread = threading.Thread(
        target=lambda: ready.put(process.stdout.readline().strip()), daemon=True
    )
    thread.start()

    def token(user_id):
        return jwt.encode(
            {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "isolated-http-crash-test-secret-only",
            algorithm="HS256",
        )

    try:
        status = ready.get(timeout=45)
        assert status.startswith("READY:"), status
        port = int(status.split(":")[1])
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
        ) as client:
            path = f"/v1/agent-runs/{run_id}/trace"
            assert client.get(path).status_code == 401
            assert (
                client.get(path, headers={"Authorization": f"Bearer {token(other_id)}"}).status_code
                == 404
            )
            assert (
                client.get(
                    path, headers={"Authorization": f"Bearer {token(uuid.uuid4())}"}
                ).status_code
                == 401
            )
            headers = {"Authorization": f"Bearer {token(owner_id)}"}
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["write_receipts"][0]["state"] == (
                "committed" if committed else "unconfirmed"
            )
            events = client.get(f"/v1/agent-runs/{run_id}/events", headers=headers)
            assert events.status_code == 200
            assert events.json()["status"] == "unconfirmed"
            assert events.json()["may_repeat_writes"] is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        thread.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize(
    "phase", ["before_handler", "before_commit", "after_commit", "after_terminal"]
)
def test_actual_approval_worker_hard_exit_preserves_commit_boundaries(tmp_path, monkeypatch, phase):
    worker = Path(__file__).parent / "fixtures" / "background_crash_worker.py"
    database_url = f"sqlite:///{(tmp_path / 'crash.sqlite').as_posix()}"
    server_url = os.environ.get("FITAGENT_CRASH_TEST_SERVER")
    if server_url:
        from sqlalchemy.engine import make_url

        url = make_url(server_url)
        if url.host != "127.0.0.1" or url.port != 15433 or url.database != "postgres":
            raise ValueError("Dedicated crash server only")
        database_url = url.set(database="fitagent_crash_" + uuid.uuid4().hex).render_as_string(
            hide_password=False
        )
    child_env = {**os.environ, "USE_PGVECTOR": "false", "LANGCHAIN_TRACING_V2": "false"}
    process = subprocess.Popen(
        [sys.executable, "-u", str(worker), str(tmp_path), phase, database_url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_env,
    )
    ready = queue.Queue()
    thread = threading.Thread(
        target=lambda: ready.put(process.stdout.readline().strip()), daemon=True
    )
    thread.start()
    try:
        assert ready.get(timeout=45) == "READY"
        assert process.poll() is None
        process.kill()  # Exact Popen child, never a broad process-name kill.
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        thread.join(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    engine = create_engine(database_url)
    with Session(engine) as db:
        user = db.scalar(select(models.User).where(models.User.email == "crash@example.test"))
        other = db.scalar(
            select(models.User).where(models.User.email == "other-crash@example.test")
        )
        plan = db.scalar(select(models.TrainingPlan))
        approval = db.scalar(select(models.PendingApproval))
        job = db.scalar(select(models.BackgroundTask))
        identity = uuid.UUID(job.payload_json["execution_trace_run_id"])
        page = stream_event_page(db, identity, user.id)
        committed = phase in {"after_commit", "after_terminal"}
        assert plan.plan_json["training_days"][0]["exercises"][0]["sets"] == (3 if committed else 4)
        assert approval.status == ("executed" if committed else "approved")
        assert job.status == ("completed" if committed else "running")
        assert page["status"] == ("completed" if phase == "after_terminal" else "unconfirmed")
        assert page["events"][0]["event"]["type"] == "background.start"
        assert page["may_repeat_writes"] is False
        receipt = approved_plan_write_receipt(db, user.id, approval.id)
        assert receipt["state"] == ("committed" if committed else "unconfirmed")
        trace = recorded_execution_trace(db, identity, user.id)
        assert trace["write_receipts"] == [receipt]
        if phase in {"before_commit", "after_commit"}:
            verify_real_http_restart(tmp_path, user.id, other.id, identity, committed, database_url)
        # Opening logs or running the worker again must not repeat this write.
        assert run_one_background_task(db, user_id=user.id) is None
        db.refresh(plan)
        assert plan.plan_json["training_days"][0]["exercises"][0]["sets"] == (3 if committed else 4)
    engine.dispose()
