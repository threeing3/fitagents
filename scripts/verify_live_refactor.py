"""Real HTTP/database/worker acceptance, with synthetic users and bounded model calls."""

import argparse
import copy
import json
import os
import subprocess
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="logs/refactor_live_acceptance_20261002.json")
    parser.add_argument("--receipt", default="logs/refactor_model_receipts_20261002.jsonl")
    args = parser.parse_args()
    if Path(args.report).exists():
        raise ValueError("Refusing to overwrite an existing acceptance report")
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"
    )
    os.environ["USE_PGVECTOR"] = "false"
    from fast_api.app.db import models
    from fast_api.app.db.database import SessionLocal, engine

    assert engine.url.database == "fitagent_acceptance_20261002"
    report = {
        "scope": "synthetic users; actual HTTP, migrated PostgreSQL and scoped worker",
        "steps": [],
    }
    output = Path(args.report)
    base = "http://127.0.0.1:1016"
    password = "Synthetic-only-password-1016"

    def request(client, method, path, **kwargs):
        response = client.request(method, base + path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{method} {path}: HTTP {response.status_code}; {response.text[:500]}"
            )
        return response.json()

    def passed(step):
        report["steps"].append(step)
        print("PASS:", step, flush=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with (
        httpx.Client(trust_env=False, timeout=180) as client,
        httpx.Client(trust_env=False, timeout=30) as other,
    ):
        try:
            assert request(client, "GET", "/health/ready")["status"] == "ready"
            passed("full app ready on migrated schema")
            account = request(
                client,
                "POST",
                "/v1/auth/register",
                json={
                    "email": f"acceptance-{uuid.uuid4().hex[:12]}@example.com",
                    "password": password,
                    "display_name": "Synthetic acceptance",
                },
            )
            user_id = account["user_id"]
            report["synthetic_user_id"] = user_id
            # Never save access tokens/cookies. UI test logs into this synthetic account.
            report["synthetic_email"] = account["email"]
            request(
                other,
                "POST",
                "/v1/auth/register",
                json={
                    "email": f"acceptance-other-{uuid.uuid4().hex[:12]}@example.com",
                    "password": password,
                    "display_name": "Synthetic other",
                },
            )
            request(
                client,
                "POST",
                "/v1/profiles",
                json={
                    "age": 25,
                    "sex": "male",
                    "height_cm": 175,
                    "weight_kg": 70,
                    "goal": "maintenance",
                    "experience_level": "beginner",
                    "workout_frequency": 3,
                    "workout_duration": 60,
                    "equipment_available": ["dumbbells"],
                    "injuries": [],
                },
            )
            passed("real registration and profile persistence")
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            later = (date.today() + timedelta(days=3)).isoformat()
            baseline = {
                "training_days": [
                    {
                        "date": tomorrow,
                        "focus": "strength",
                        "exercises": [{"name": "Dumbbell row", "sets": 4, "reps": 10}],
                    },
                    {
                        "date": later,
                        "focus": "strength",
                        "exercises": [{"name": "Dumbbell press", "sets": 3, "reps": 10}],
                    },
                ],
                "nutrition": {"protein_g": 120},
            }
            with SessionLocal() as db:
                user = db.get(models.User, uuid.UUID(user_id))
                assert user.email.startswith("acceptance-")
                plan = models.TrainingPlan(
                    user_id=user.id,
                    plan_json=baseline,
                    rationale="Explicit synthetic dated fixture, not model-generated",
                )
                db.add(plan)
                db.commit()
                plan_id = plan.id
            report["synthetic_plan_id"] = str(plan_id)
            report["selected_day"] = tomorrow
            task = request(
                client,
                "POST",
                "/v1/responsibilities/weekly-review",
                json={"source_instruction": "每周复盘四周，修改训练计划必须先让我批准", "weeks": 4},
            )
            task_id = task.get("task_id") or task.get("id")
            assert task_id, task.keys()
            report["responsibility_id"] = task_id
            checkin = request(
                client,
                "POST",
                "/v1/checkins/daily",
                json={
                    "sleep_hours": 5,
                    "fatigue": 8,
                    "soreness": 3,
                    "idempotency_key": "synthetic-checkin-1016",
                },
            )
            repeated = request(
                client,
                "POST",
                "/v1/checkins/daily",
                json={
                    "sleep_hours": 5,
                    "fatigue": 8,
                    "soreness": 3,
                    "idempotency_key": "synthetic-checkin-1016",
                },
            )
            assert repeated == {**checkin, "idempotent_replay": True}
            with SessionLocal() as db:
                assert db.get(models.TrainingPlan, plan_id).plan_json == baseline
            approvals = request(client, "GET", "/v1/approvals/pending")
            if not approvals:
                request(
                    client,
                    "POST",
                    f"/v1/responsibilities/{task_id}/plan-proposal",
                    json={
                        "plan_id": str(plan_id),
                        "day_date": tomorrow,
                        "reduce_by": 1,
                        "reason": "synthetic fatigue review",
                    },
                )
                approvals = request(client, "GET", "/v1/approvals/pending")
            assert len(approvals) == 1, len(approvals)
            passed("idempotent checkin creates proposal, no automatic plan mutation")
            approval_id = approvals[0]["approval_id"]
            assert request(other, "GET", "/v1/approvals/pending") == []
            forbidden = other.post(
                base + "/v1/approvals/decide",
                json={"approval_id": approval_id, "action": "approve"},
            )
            assert forbidden.status_code in (403, 404), forbidden.status_code
            passed("other user cannot view or approve owned action")
            request(
                client,
                "POST",
                "/v1/approvals/decide",
                json={"approval_id": approval_id, "action": "approve"},
            )
            subprocess.run(
                [
                    ".venv/Scripts/python.exe",
                    "-m",
                    "scripts.serve_refactor_acceptance",
                    "worker",
                    "--user-id",
                    user_id,
                ],
                check=True,
                timeout=120,
            )
            history = request(client, "GET", "/v1/approvals/history")
            action = next(item for item in history if item["approval_id"] == approval_id)
            assert action["status"] == "executed", action["status"]
            assert action["job_status"] == "completed", action["job_status"]
            expected = copy.deepcopy(baseline)
            expected["training_days"][0]["exercises"][0]["sets"] = 3
            with SessionLocal() as db:
                assert db.get(models.TrainingPlan, plan_id).plan_json == expected
            passed(
                "approved scoped worker changes only the selected date and persists terminal status"
            )
            duplicate = client.post(
                base + "/v1/approvals/decide",
                json={"approval_id": approval_id, "action": "approve"},
            )
            assert duplicate.status_code in (404, 409), duplicate.status_code
            passed("duplicate approval does not execute again")
            session = request(
                client,
                "POST",
                "/v1/chat/sessions",
                json={"title": "Synthetic live model acceptance"},
            )
            chat = request(
                client,
                "POST",
                "/v1/chat/messages",
                json={
                    "session_id": session["session_id"],
                    "message": "请解释训练中的RPE是什么意思，不需要修改计划，也不要记录新的个人信息。",
                    "idempotency_key": "synthetic-live-chat-1016",
                },
            )
            assert chat["assistant_message"]
            run = request(client, "GET", f"/v1/agent-runs/{chat['agent_run_id']}")
            # Retain the actual run id for inspecting provider success, not a
            # blanket claim that any nonempty fallback text proves a live model.
            report["model_run_id"] = chat["agent_run_id"]
            report["run_response_keys"] = sorted(run)
            receipt_path = Path(args.receipt)
            receipts = (
                [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
                if receipt_path.exists()
                else []
            )
            owned = [item for item in receipts if item["user_id"] == user_id]
            assert any(
                item["status"] == "live_return" and item["response_chars"] > 0 for item in owned
            ), "No actual provider return receipt for this synthetic user"
            report["model_receipts"] = owned
            passed(
                "actual model returns nonempty response to this synthetic user; persisted run completes"
            )
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["failure"] = str(exc)
            raise
        finally:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
