"""CLI for the guided learning mode.

Examples::

    python -m algorithm.learning.mode list
    python -m algorithm.learning.mode show 03_intent_and_routing
    python -m algorithm.learning.mode start 03_intent_and_routing
    python -m algorithm.learning.mode check 03_intent_and_routing
    python -m algorithm.learning.mode master 03_intent_and_routing --evidence "新增风险样例并解释混淆矩阵"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .curriculum import CURRICULUM, get_module
from .progress import ProgressStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROGRESS = REPO_ROOT / "algorithm" / "research_state" / "learning_progress.json"


def _store(path: Path | None) -> ProgressStore:
    return ProgressStore(path or DEFAULT_PROGRESS)


def _check_files(module_id: str) -> list[str]:
    card = get_module(module_id)
    return [path for path in card.files if not (REPO_ROOT / path).exists()]


def check_module(module_id: str) -> dict[str, Any]:
    """Run lightweight, deterministic checks without launching GPU training."""

    card = get_module(module_id)
    checks: list[dict[str, Any]] = []
    missing = _check_files(module_id)
    checks.append({"name": "course files exist", "passed": not missing, "details": missing})

    if module_id == "02_dataset_governance":
        manifest = REPO_ROOT / "algorithm/datasets/manifests/validation.json"
        try:
            report = json.loads(manifest.read_text(encoding="utf-8"))
            passed = (
                report.get("error_count") == 0
                and report.get("rows", 0) > 0
                and not report.get("user_split_leaks")
            )
            details: Any = {
                "rows": report.get("rows"),
                "error_count": report.get("error_count"),
                "user_split_leaks": report.get("user_split_leaks", {}),
            }
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            passed, details = False, str(exc)
        checks.append({"name": "validated dataset report", "passed": passed, "details": details})
    elif module_id == "03_intent_and_routing":
        try:
            from algorithm.app_algorithms.intent_baseline import evaluate_cases

            cases_path = REPO_ROOT / "tests/evals/intent_eval_cases.json"
            result = evaluate_cases(json.loads(cases_path.read_text(encoding="utf-8")))
            passed = result["macro_f1"] >= 0.85 and result["risk_recall"] >= 0.98
            details = {
                "cases": result["cases"],
                "accuracy": result["accuracy"],
                "macro_f1": result["macro_f1"],
                "risk_recall": result["risk_recall"],
            }
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append({"name": "intent macro-f1 threshold", "passed": passed, "details": details})
    elif module_id == "06_business_modeling":
        try:
            from algorithm.business.business_baseline import run_business_baseline

            report = run_business_baseline(count=120, seed=42)
            model_metrics = report["acceptance_model"]["metrics"]
            passed = (
                report["dataset"]["source_counts"] == {"synthetic": 120}
                and report["dataset"]["label_source_counts"] == {"simulated_outcome": 120}
                and model_metrics["support"] > 0
            )
            details = {
                "model": report["acceptance_model"]["class"],
                "auroc": model_metrics["auroc"],
                "f1": model_metrics["f1"],
                "notes": report["notes"],
            }
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append({"name": "simulated business baseline", "passed": passed, "details": details})
    elif module_id == "04_retrieval_and_reranking":
        try:
            from algorithm.app_algorithms.memory_retrieval_eval import evaluate_retrieval_records

            cases_path = REPO_ROOT / "tests/evals/retrieval_eval_cases.json"
            report = evaluate_retrieval_records(
                json.loads(cases_path.read_text(encoding="utf-8")), k=5
            )
            passed = (
                report["strategies"]["bm25"]["available"]
                and report["strategies"]["hybrid"]["available"]
            )
            details = {
                "bm25_recall_at_5": report["strategies"]["bm25"]["recall_at_k"],
                "hybrid_recall_at_5": report["strategies"]["hybrid"]["recall_at_k"],
                "vector_available": report["strategies"]["vector"]["available"],
                "embedding_policy": report["embedding_policy"],
            }
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append(
            {"name": "retrieval baseline comparison", "passed": passed, "details": details}
        )
    elif module_id == "05_tool_planning":
        try:
            from algorithm.app_algorithms.response_reranker import select_best_candidate
            from algorithm.app_algorithms.tool_plan_eval import (
                schema_valid_rate,
                tool_selection_accuracy,
                tool_sequence_accuracy,
            )

            plans = [
                {
                    "selected_tools": ["context.build", "plan.validate"],
                    "tool_sequence": ["context.build", "plan.validate"],
                    "plan_valid": True,
                    "risk_level": "low",
                },
                {
                    "selected_tools": ["context.build"],
                    "tool_sequence": ["context.build"],
                    "plan_valid": True,
                    "risk_level": "low",
                },
            ]
            records = [
                {
                    "predicted_tools": ["context.build", "plan.validate"],
                    "expected_tools": ["plan.validate", "context.build"],
                    "predicted_sequence": ["context.build", "plan.validate"],
                    "expected_sequence": ["context.build", "plan.validate"],
                },
                {
                    "predicted_tools": ["context.build"],
                    "expected_tools": ["context.build"],
                    "predicted_sequence": ["context.build"],
                    "expected_sequence": ["context.build"],
                },
            ]
            selected, _ = select_best_candidate(
                ["请带着锐痛继续训练。", "如果出现锐痛，请停止动作并咨询专业人士。"]
            )
            passed = (
                schema_valid_rate(plans) == 1.0
                and tool_selection_accuracy(records) == 1.0
                and selected != "请带着锐痛继续训练。"
            )
            details = {
                "schema_valid_rate": schema_valid_rate(plans),
                "tool_selection_accuracy": tool_selection_accuracy(records),
                "tool_sequence_accuracy": tool_sequence_accuracy(records),
                "safety_candidate_selected": selected,
            }
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append(
            {"name": "tool planning and safety gate", "passed": passed, "details": details}
        )
    elif module_id == "07_sft_and_dpo":
        requirements = REPO_ROOT / "algorithm/training/requirements-training.txt"
        configs = [
            REPO_ROOT / "algorithm/training/configs/intent_qwen3_4b_qlora.json",
            REPO_ROOT / "algorithm/training/configs/dpo_qwen3b.json",
        ]
        try:
            from algorithm.training.sft.train_qlora import load_config
            from algorithm.training.sft.train_qlora import (
                train as train_sft,
            )

            sft_config_path = configs[0]
            sft_config = {
                **load_config(sft_config_path),
                "train_dataset_path": "algorithm/datasets/fixtures/sft_smoke.jsonl",
                "eval_dataset_path": "algorithm/datasets/fixtures/sft_smoke_validation.jsonl",
            }
            dataset_source = "versioned_smoke_fixtures"
            sft_summary = train_sft(sft_config, sft_config_path, dry_run=True)
            sft_ready = sft_summary["train_rows"] > 0 and sft_summary["eval_rows"] > 0
            dpo_path = REPO_ROOT / "algorithm/datasets/manifests/preference_pairs.jsonl"
            dpo_ready = dpo_path.exists() and dpo_path.stat().st_size > 0
            details = {
                "configs": [str(path) for path in configs],
                "sft_train_rows": sft_summary["train_rows"],
                "sft_eval_rows": sft_summary["eval_rows"],
                "dataset_source": dataset_source,
                "dpo_ready": dpo_ready,
                "dpo_note": "需要审核后的 chosen/rejected 才能运行",
            }
            passed = requirements.exists() and all(path.exists() for path in configs) and sft_ready
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append(
            {"name": "training dependency/config dry-run", "passed": passed, "details": details}
        )
    elif module_id == "08_evaluation_and_interview":
        log_dir = REPO_ROOT / "logs/experiments"
        logs = sorted([*log_dir.glob("*.md"), *log_dir.glob("*.log")]) if log_dir.exists() else []
        checks.append(
            {
                "name": "readable experiment log exists",
                "passed": bool(logs),
                "details": [str(path.relative_to(REPO_ROOT)) for path in logs],
            }
        )

    return {
        "module_id": card.module_id,
        "title": card.title,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def _print_card(module_id: str) -> None:
    card = get_module(module_id)
    print(f"[{card.module_id}] {card.title} · {card.track}")
    print(f"目标：{card.objective}")
    print(f"前置：{', '.join(card.prerequisites) or '无'}")
    print("核心概念：" + "、".join(card.concepts))
    print("代码与资料：")
    for item in card.files:
        print(f"  - {item}")
    print("实验命令：")
    for item in card.commands:
        print(f"  $ {item}")
    print("动手练习：")
    for index, item in enumerate(card.exercises, 1):
        print(f"  {index}. {item}")
    print("面试自测：")
    for item in card.questions:
        print(f"  ? {item}")
    print("完成标准：" + "；".join(card.acceptance))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Fitness Coach guided algorithm learning mode")
    parser.add_argument("--progress", type=Path, default=None, help="custom progress JSON path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list curriculum modules")
    sub.add_parser("progress", help="show learning progress")
    sub.add_parser("next", help="show the next unfinished module")
    sub.add_parser("init", help="create a progress file without overwriting it")
    show = sub.add_parser("show", help="show one learning card")
    show.add_argument("module_id")
    start = sub.add_parser("start", help="mark a module as in progress")
    start.add_argument("module_id")
    check = sub.add_parser("check", help="run deterministic module checks")
    check.add_argument("module_id")
    master = sub.add_parser("master", help="mark a module mastered after checks")
    master.add_argument("module_id")
    master.add_argument("--evidence", required=True, help="what you implemented or measured")
    master.add_argument("--note", default="", help="remaining doubt or next action")
    args = parser.parse_args(argv)
    store = _store(args.progress)

    if args.command == "list":
        state = store.load()
        for card in CURRICULUM:
            status = state["modules"][card.module_id]["status"]
            print(f"{card.module_id:28} [{status:12}] {card.title}")
        return 0
    if args.command == "progress":
        state = store.load()
        mastered = sum(value["status"] == "mastered" for value in state["modules"].values())
        print(f"完成度：{mastered}/{len(CURRICULUM)} ({mastered / len(CURRICULUM):.0%})")
        for card in CURRICULUM:
            value = state["modules"][card.module_id]
            print(
                f"- {card.module_id}: {value['status']}"
                + (f" · {value['evidence']}" if value.get("evidence") else "")
            )
        return 0
    if args.command == "init":
        if store.path.exists():
            print(f"progress already exists: {store.path}")
        else:
            store.save(store.load())
            print(f"created: {store.path}")
        return 0
    if args.command == "show":
        _print_card(args.module_id)
        return 0
    if args.command == "next":
        state = store.load()
        for card in CURRICULUM:
            if state["modules"][card.module_id]["status"] != "mastered":
                _print_card(card.module_id)
                return 0
        print("全部模块已完成。请重新跑一轮错误分析并准备面试演示。")
        return 0
    if args.command == "start":
        store.update(args.module_id, "in_progress")
        print(f"started: {args.module_id}")
        return 0
    if args.command == "check":
        result = check_module(args.module_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "master":
        result = check_module(args.module_id)
        if not result["passed"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("验收未通过：先完成课程卡中的实验，再记录 evidence。")
            return 1
        store.update(args.module_id, "mastered", args.evidence, args.note)
        print(f"mastered: {args.module_id}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
