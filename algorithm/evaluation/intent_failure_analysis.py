"""Aggregate cross-path intent failures without exposing fixed-test prompts."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CHECK_NAMES = ("primary_intent", "secondary_intents", "risk_level", "clarification")
PATH_ORDER = ("rule_only", "deepseek_all", "hybrid")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _safe_case(row: dict[str, Any]) -> dict[str, Any]:
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    return {
        "case_id": str(row.get("case_id") or ""),
        "category": str(row.get("category") or "unknown"),
        "exact_pass": bool(row.get("exact_pass")),
        "checks": {name: bool(checks.get(name)) for name in CHECK_NAMES},
    }


def _path_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    passed = sum(row["exact_pass"] for row in rows)
    check_scores = {
        name: round(sum(row["checks"][name] for row in rows) / count, 4) if count else 0.0
        for name in CHECK_NAMES
    }
    return {
        "cases": count,
        "exact_passed": passed,
        "exact_pass_rate": round(passed / count, 4) if count else 0.0,
        "check_scores": check_scores,
    }


def analyze_failures(path_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Return aggregate failure taxonomy for aligned fixed-test predictions."""

    normalized = {
        name: [_safe_case(row) for row in rows]
        for name, rows in path_rows.items()
        if name in PATH_ORDER
    }
    if set(normalized) != set(PATH_ORDER):
        raise ValueError(f"expected paths {PATH_ORDER}, got {tuple(normalized)}")
    indexed = {name: {row["case_id"]: row for row in rows} for name, rows in normalized.items()}
    case_ids = set(indexed[PATH_ORDER[0]])
    if not case_ids or any(set(indexed[name]) != case_ids for name in PATH_ORDER[1:]):
        raise ValueError("all paths must contain the same non-empty case_id set")

    categories: dict[str, list[str]] = defaultdict(list)
    for case_id, row in indexed[PATH_ORDER[0]].items():
        categories[row["category"]].append(case_id)

    category_rows: list[dict[str, Any]] = []
    for category, ids in sorted(categories.items()):
        paths: dict[str, Any] = {}
        for path_name in PATH_ORDER:
            rows = [indexed[path_name][case_id] for case_id in ids]
            paths[path_name] = _path_summary(rows)
        best_rate = max(item["exact_pass_rate"] for item in paths.values())
        best_paths = [name for name in PATH_ORDER if paths[name]["exact_pass_rate"] == best_rate]
        failed_checks: Counter[str] = Counter()
        for case_id in ids:
            for name, passed in indexed["deepseek_all"][case_id]["checks"].items():
                if not passed:
                    failed_checks[name] += 1
        category_rows.append(
            {
                "category": category,
                "cases": len(ids),
                "paths": paths,
                "best_observed_paths": best_paths,
                "dominant_deepseek_failure": (
                    failed_checks.most_common(1)[0][0] if failed_checks else None
                ),
                "actionability": "diagnostic_only_do_not_tune_on_test",
            }
        )

    transitions = {}
    for target in ("deepseek_all", "hybrid"):
        rescued = sum(
            not indexed["rule_only"][case_id]["exact_pass"]
            and indexed[target][case_id]["exact_pass"]
            for case_id in case_ids
        )
        regressed = sum(
            indexed["rule_only"][case_id]["exact_pass"]
            and not indexed[target][case_id]["exact_pass"]
            for case_id in case_ids
        )
        transitions[target] = {"rescued_from_rule": rescued, "regressed_from_rule": regressed}

    return {
        "schema_version": "fitagent-intent-failure-taxonomy/v1",
        "source": "fixed_challenge_test_aggregate",
        "partition": "test",
        "training_eligible": False,
        "contains_user_messages": False,
        "cases": len(case_ids),
        "paths": {name: _path_summary(normalized[name]) for name in PATH_ORDER},
        "transitions": transitions,
        "categories": category_rows,
        "next_data_contract": {
            "required_partition": "development",
            "must_not_copy_fixed_test_prompts": True,
            "required_failure_fields": list(CHECK_NAMES),
            "minimum_cases_per_priority_category": 30,
        },
        "limitations": [
            "This taxonomy diagnoses a frozen test set and must not be used to tune routing thresholds.",
            "Qwen3 adapter evidence is aggregate-only in the published artifact, so per-case transitions cover rules and DeepSeek paths only.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a sample-free intent failure taxonomy")
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument("--deepseek", type=Path, required=True)
    parser.add_argument("--hybrid", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze_failures(
        {
            "rule_only": _read_jsonl(args.rule),
            "deepseek_all": _read_jsonl(args.deepseek),
            "hybrid": _read_jsonl(args.hybrid),
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
