"""Evaluate deterministic clarification rules on the isolated development set."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fast_api.app.services.clarification_protocol import ClarificationProtocolValidator
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter

ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "algorithm" / "datasets" / "development" / "intent_dev_v1.json"
ISOLATION_PATH = (
    ROOT
    / "algorithm"
    / "evaluation"
    / "reports"
    / "intent_development_isolation_20260826.summary.json"
)
REPORT_PATH = (
    ROOT
    / "algorithm"
    / "evaluation"
    / "reports"
    / "intent_development_protocol_20260826.summary.json"
)
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _checks(decision: IntentDecision, row: dict[str, Any]) -> dict[str, bool]:
    required_secondary = set(row["required_secondary_intents"])
    return {
        "primary_intent": decision.primary_intent == row["expected_primary_intent"],
        "secondary_intents": required_secondary.issubset(set(decision.secondary_intents)),
        "risk_level": RISK_ORDER[decision.risk_level] >= RISK_ORDER[row["minimum_risk_level"]],
        "clarification": decision.needs_clarification == row["expected_clarification"],
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks = tuple(rows[0]["checks"])
    return {
        "cases": len(rows),
        "exact_pass_rate": round(sum(all(row["checks"].values()) for row in rows) / len(rows), 4),
        "check_scores": {
            check: round(sum(row["checks"][check] for row in rows) / len(rows), 4)
            for check in checks
        },
    }


def evaluate() -> dict[str, Any]:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    isolation = json.loads(ISOLATION_PATH.read_text(encoding="utf-8"))
    if not isolation.get("passed"):
        raise RuntimeError("Development-set isolation gate did not pass.")

    router = IntentRouter()
    validator = ClarificationProtocolValidator()
    evaluated: dict[str, list[dict[str, Any]]] = {"rule_only": [], "rule_with_protocol": []}
    reason_counts: Counter[str] = Counter()
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in dataset:
        baseline = router.analyze(row["user_message"])
        protocol_decision = router.analyze(row["user_message"])
        result = validator.validate(row["user_message"], protocol_decision)
        validator.apply(protocol_decision, result, router)
        reason_counts.update(result.reason_codes)
        for path, decision in (("rule_only", baseline), ("rule_with_protocol", protocol_decision)):
            item = {"category": row["category"], "checks": _checks(decision, row)}
            evaluated[path].append(item)
            categories[f"{row['category']}:{path}"].append(item)

    return {
        "schema_version": "fitagent-intent-development-protocol/v1",
        "status": "diagnostic_verified",
        "dataset": {
            "name": "intent_dev_v1",
            "cases": len(dataset),
            "partition": "development",
            "source": "curated_development_template",
            "training_eligible": False,
            "human_review_status": "not_reviewed",
            "contains_user_messages": False,
        },
        "isolation": {
            "passed": True,
            "exact_overlap_count": isolation["normalized_exact_overlap"],
            "maximum_character_5gram_jaccard": isolation["max_char_5gram_jaccard"],
        },
        "paths": {path: _aggregate(rows) for path, rows in evaluated.items()},
        "categories": {
            category: {path: _aggregate(categories[f"{category}:{path}"]) for path in evaluated}
            for category in sorted({row["category"] for row in dataset})
        },
        "protocol_reason_counts": dict(sorted(reason_counts.items())),
        "field_router": {
            "primary_intent_threshold": 0.8,
            "secondary_intents_threshold": 0.75,
            "risk_authority": "deterministic_rules",
            "low_confidence_action": "request_deepseek_field_review",
            "evaluated_with_live_adapter": False,
        },
        "claims": {
            "test_set_used_for_tuning": False,
            "production_uplift": False,
            "human_reviewed": False,
        },
        "limitations": [
            "The development cases are curated templates and have not been human reviewed.",
            "This report validates protocol behavior, not live Qwen3 adapter calibration.",
            "The frozen test set remains untouched and must be used only for release evaluation.",
        ],
    }


def main() -> None:
    report = evaluate()
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
