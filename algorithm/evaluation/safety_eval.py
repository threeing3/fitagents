"""Hard-gate safety evaluation against the existing deterministic checks."""

from __future__ import annotations

from typing import Any

from fast_api.app.core.eval_metrics import check_safety_rules


def evaluate_responses(responses: list[str]) -> dict[str, Any]:
    details = []
    for response in responses:
        result = check_safety_rules(response)
        checks = result.get("checks", {})
        details.append(
            {
                "safe": bool(checks.get("no_harmful_content", False)),
                "length_ok": bool(checks.get("response_length_ok", False)),
                "checks": checks,
            }
        )
    safe = sum(item["safe"] for item in details)
    return {
        "count": len(details),
        "safe_rate": safe / len(details) if details else 0.0,
        "details": details,
        "hard_gate_passed": all(item["safe"] for item in details),
    }
