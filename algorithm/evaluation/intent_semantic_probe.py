"""Run a synthetic development diagnostic against the deterministic intent paths."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.inference.intent_catalog import AgentIntentCatalog
from fast_api.app.services.clarification_protocol import ClarificationProtocolValidator
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "algorithm/datasets/development/intent_semantic_probe_v1.json"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
PATHS = ("rule_only", "rule_with_protocol")


def load_cases(path: Path = DEFAULT_INPUT) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("partition") != "development_diagnostic":
        raise ValueError("probe must be a development diagnostic, not a final test")
    if dataset.get("training_eligible") is not False:
        raise ValueError("probe cases must not be used for training")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("probe cases are missing")
    seen: set[str] = set()
    for row in cases:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"missing or duplicate case_id: {case_id}")
        seen.add(case_id)
        required = set(row.get("required_intents", []))
        forbidden = set(row.get("forbidden_intents", []))
        if not required or required & forbidden:
            raise ValueError(f"invalid required/forbidden intents: {case_id}")
        if not (required | forbidden).issubset(AgentIntentCatalog.VALID_INTENTS):
            raise ValueError(f"unknown intent label: {case_id}")
        low = row.get("minimum_risk_level")
        high = row.get("maximum_risk_level")
        if low not in RISK_ORDER or high not in RISK_ORDER or RISK_ORDER[low] > RISK_ORDER[high]:
            raise ValueError(f"invalid risk bounds: {case_id}")
        if not isinstance(row.get("generate_plan_allowed"), bool):
            raise ValueError(f"missing plan permission expectation: {case_id}")
        if not isinstance(row.get("user_message"), str) or not row["user_message"]:
            raise ValueError(f"missing message: {case_id}")
    return dataset, cases


def check_decision(row: dict[str, Any], decision: IntentDecision) -> dict[str, bool]:
    predicted = {decision.primary_intent, *decision.secondary_intents}
    return {
        "required_intents": set(row["required_intents"]) <= predicted,
        "forbidden_intents": not (set(row["forbidden_intents"]) & predicted),
        "risk_bounds": RISK_ORDER[row["minimum_risk_level"]]
        <= RISK_ORDER[decision.risk_level]
        <= RISK_ORDER[row["maximum_risk_level"]],
        "plan_permission": decision.allowed_actions.get("generate_plan", False)
        is row["generate_plan_allowed"],
    }


def evaluate(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    router = IntentRouter()
    validator = ClarificationProtocolValidator()
    details: list[dict[str, Any]] = []
    for row in cases:
        for path in PATHS:
            decision = router.analyze(row["user_message"])
            if path == "rule_with_protocol":
                validation = validator.validate(row["user_message"], decision)
                validator.apply(decision, validation, router)
            checks = check_decision(row, decision)
            details.append(
                {
                    "case_id": row["case_id"],
                    "family": row["family"],
                    "path": path,
                    "user_message": row["user_message"],
                    "expected": {
                        key: row[key]
                        for key in (
                            "required_intents",
                            "forbidden_intents",
                            "minimum_risk_level",
                            "maximum_risk_level",
                            "generate_plan_allowed",
                        )
                    },
                    "predicted": {
                        "primary_intent": decision.primary_intent,
                        "secondary_intents": decision.secondary_intents,
                        "risk_level": decision.risk_level,
                        "needs_clarification": decision.needs_clarification,
                        "generate_plan_allowed": decision.allowed_actions.get(
                            "generate_plan", False
                        ),
                        "task_plan": decision.task_plan,
                    },
                    "checks": checks,
                    "all_checks_pass": all(checks.values()),
                }
            )
    summary: dict[str, Any] = {
        "schema_version": "fitagent-intent-semantic-probe-report/v1",
        "partition": "development_diagnostic",
        "source": "assistant_authored_synthetic",
        "human_review_status": "not_reviewed",
        "training_eligible": False,
        "cases": len(cases),
        "paths": {},
        "claim_boundary": "Unreviewed synthetic diagnostic; not a final test or production result.",
    }
    for path in PATHS:
        subset = [item for item in details if item["path"] == path]
        failures = [item for item in subset if not item["all_checks_pass"]]
        summary["paths"][path] = {
            "all_checks_passed": len(subset) - len(failures),
            "all_checks_rate": round((len(subset) - len(failures)) / len(subset), 4),
            "check_passed": {
                name: sum(item["checks"][name] for item in subset) for name in subset[0]["checks"]
            },
            "failure_case_ids": [item["case_id"] for item in failures],
            "failure_families": dict(Counter(item["family"] for item in failures)),
        }
    return details, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dataset, cases = load_cases(args.input)
    details, summary = evaluate(cases)
    summary["dataset_schema_version"] = dataset["schema_version"]
    details_path = args.output_dir / "cases.jsonl"
    summary_path = args.output_dir / "summary.json"
    for path in (details_path, summary_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite prior diagnostic: {path}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    details_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in details),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
