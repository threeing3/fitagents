"""Verify a deployed intent service without persisting its credential or prompts."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


def evaluate_checks(
    live_status: int,
    ready_status: int,
    unauthorized_status: int,
    safe_payload: dict[str, Any],
    risk_payload: dict[str, Any],
) -> dict[str, bool]:
    safe_decision = safe_payload.get("decision", {})
    risk_decision = risk_payload.get("decision", {})
    return {
        "live": live_status == 200,
        "ready": ready_status == 200,
        "unauthorized_rejected": unauthorized_status == 401,
        "safe_schema_valid": safe_payload.get("schema_version") == "intent_decision_v2"
        and bool(safe_decision.get("primary_intent")),
        "risk_schema_valid": risk_payload.get("schema_version") == "intent_decision_v2"
        and risk_decision.get("primary_intent") == "injury_or_risk"
        and risk_decision.get("risk_level") in {"high", "critical"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed FitAgent intent service")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--report")
    args = parser.parse_args()
    key = os.getenv("INTENT_INFERENCE_KEY")
    if not key:
        raise SystemExit("INTENT_INFERENCE_KEY must be provided through the environment")

    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}
    started = time.perf_counter()
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        live = client.get(f"{base_url}/health/live")
        ready = client.get(f"{base_url}/health/ready")
        unauthorized = client.post(f"{base_url}/v1/intent/classify", json={"message": "健康检查"})
        safe = client.post(
            f"{base_url}/v1/intent/classify",
            headers=headers,
            json={
                "message": "请帮我安排明天的力量训练",
                "rule_decision": {"primary_intent": "training_plan", "risk_level": "low"},
            },
        )
        safe.raise_for_status()
        risk = client.post(
            f"{base_url}/v1/intent/classify",
            headers=headers,
            json={
                "message": "我胸闷并且呼吸困难，但还想继续冲刺训练",
                "rule_decision": {
                    "primary_intent": "injury_or_risk",
                    "risk_level": "high",
                },
            },
        )
        risk.raise_for_status()

    checks = evaluate_checks(
        live.status_code,
        ready.status_code,
        unauthorized.status_code,
        safe.json(),
        risk.json(),
    )
    report = {
        "schema_version": "fitagent-intent-service-verification/v1",
        "base_url": base_url,
        "checks": checks,
        "passed": all(checks.values()),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "model_version": ready.json().get("model_version"),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
