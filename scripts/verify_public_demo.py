"""Bounded public smoke checks using synthetic accounts; never print credentials."""

import argparse
import json
import os
import uuid

import httpx

BASE = "https://fitagent-demo.onrender.com"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "read"])
    parser.add_argument("identity")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    identity = uuid.UUID(args.identity).hex
    password = os.environ["SMOKE_PASSWORD"]
    invite = os.environ["SMOKE_INVITE"]
    email = f"public-smoke-{identity}@example.com"
    other_email = f"public-smoke-other-{identity}@example.com"
    report = {"stage": args.stage, "checks": []}
    with httpx.Client(base_url=BASE, timeout=180, trust_env=False) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()

        ready = request("GET", "/health/ready")
        assert ready["status"] == "ready"
        assert ready["checks"]["migration"] == "018_stream_journals"
        report["checks"].append("cloud schema ready")
        if args.stage == "prepare":
            denied = client.post(
                "/v1/auth/register",
                json={"email": email, "password": password, "invite_code": "wrong-code"},
            )
            assert denied.status_code == 403
            report["checks"].append("registration requires invite")
            for account_email in (email, other_email):
                request(
                    "POST",
                    "/v1/auth/register",
                    json={
                        "email": account_email,
                        "password": password,
                        "invite_code": invite,
                        "display_name": "Synthetic cloud acceptance",
                    },
                )
        token = request("POST", "/v1/auth/login", json={"email": email, "password": password})
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        if args.stage == "prepare":
            session = request("POST", "/v1/chat/sessions", json={"title": "Synthetic cloud trace"})
            result = request(
                "POST",
                "/v1/chat/messages",
                json={
                    "session_id": session["session_id"],
                    "message": "我是合成验收用户，22岁，身高178厘米，体重74公斤，训练一年，目标增肌，可以去健身房，每周3次，每次60分钟，没有伤病。",
                    "idempotency_key": f"cloud-smoke-{identity}",
                },
            )
            assert result["assistant_message"]
            run_id = result["agent_run_id"]
            report["checks"].append("real chat request completed")
        else:
            run_id = str(uuid.UUID(args.run_id))
        report["run_id"] = run_id
        page = request("GET", f"/v1/agent-runs/{run_id}/events")
        assert page["status"] == "completed" and page["recorded_count"] > 0
        assert page["may_repeat_writes"] is False
        report["recorded_count"] = page["recorded_count"]
        events = page["events"]
        while page["has_more"]:
            page = request(
                "GET", f"/v1/agent-runs/{run_id}/events", params={"cursor": page["next_cursor"]}
            )
            events.extend(page["events"])
        assert [row["position"] for row in events] == list(range(1, len(events) + 1))
        report["successful_model_calls"] = sum(
            row["event"].get("type") == "model.end" and row["event"].get("status") == "completed"
            for row in events
        )
        assert report["successful_model_calls"] > 0
        report["checks"].append("persisted model evidence and stable event positions")
        quota = request("GET", "/v1/usage/summary")
        assert quota["global_limit"] == 20
        report["global_used"] = quota["global_used"]
        report["checks"].append("global daily quota 20")
        other = request("POST", "/v1/auth/login", json={"email": other_email, "password": password})
        client.headers["Authorization"] = f"Bearer {other['access_token']}"
        denied = client.get(f"/v1/agent-runs/{run_id}/events")
        assert denied.status_code == 404
        report["checks"].append("different account cannot read trace")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
