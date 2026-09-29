"""Inspect intent-to-action representations without executing any business tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithm.evaluation.intent_semantic_probe import load_cases
from fast_api.app.services.agent_runtime import AgentPlanner
from fast_api.app.services.intent_contract import IntentDecisionV2
from fast_api.app.services.intent_decision import IntentRouter
from fast_api.app.services.intent_decision_engine import TOOLS_BY_INTENT


def audit_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    router = IntentRouter()
    planner = AgentPlanner()
    details: list[dict[str, Any]] = []
    for row in cases:
        decision = router.analyze(row["user_message"])
        planner_intent = planner.classify_intent(row["user_message"])
        intents = [decision.primary_intent, *decision.secondary_intents]
        candidate_tools = list(
            dict.fromkeys(tool for intent in intents for tool in TOOLS_BY_INTENT.get(intent, []))
        )
        contract = IntentDecisionV2.from_legacy(decision, candidate_tools=candidate_tools)
        plan_allowed = decision.allowed_actions.get("generate_plan", False)
        details.append(
            {
                "case_id": row["case_id"],
                "family": row["family"],
                "user_message": row["user_message"],
                "expected_plan_allowed": row["generate_plan_allowed"],
                "rule_primary_intent": decision.primary_intent,
                "rule_secondary_intents": decision.secondary_intents,
                "planner_primary_intent": planner_intent,
                "rule_plan_allowed": plan_allowed,
                "task_plan": decision.task_plan,
                "candidate_tools": candidate_tools,
                "requested_actions": contract.requested_actions,
                "blocked_actions": contract.blocked_actions,
                "signals": {
                    "plan_policy_matches_expectation": plan_allowed is row["generate_plan_allowed"],
                    "planner_disagrees_with_rule_primary": planner_intent
                    != decision.primary_intent,
                    "plan_tool_candidate_while_blocked": "plan.generate" in candidate_tools
                    and not plan_allowed,
                    "write_tool_candidate_on_query": "training_log" in row["forbidden_intents"]
                    and "training.log.write" in candidate_tools,
                    "blocked_action_in_requested_actions": bool(
                        set(contract.blocked_actions) & set(contract.requested_actions)
                    ),
                },
            }
        )
    summary = {
        "schema_version": "fitagent-intent-action-boundary-audit/v1",
        "partition": "development_diagnostic",
        "source": "assistant_authored_synthetic",
        "human_review_status": "not_reviewed",
        "cases": len(cases),
        "signal_counts": {
            name: sum(item["signals"][name] for item in details) for name in details[0]["signals"]
        },
        "claim_boundary": (
            "Static decision and candidate-tool audit only. No tool was executed; "
            "a candidate is not evidence of a persisted write or policy bypass."
        ),
    }
    return details, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    _, cases = load_cases(args.input)
    details, summary = audit_cases(cases)
    details_path = args.output_dir / "cases.jsonl"
    summary_path = args.output_dir / "summary.json"
    for path in (details_path, summary_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite prior audit: {path}")
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
