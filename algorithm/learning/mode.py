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
            passed = report.get("error_count") == 0 and report.get("rows", 0) > 0
            details: Any = {"rows": report.get("rows"), "error_count": report.get("error_count")}
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            passed, details = False, str(exc)
        checks.append({"name": "validated dataset report", "passed": passed, "details": details})
    elif module_id == "03_intent_and_routing":
        try:
            from algorithm.app_algorithms.intent_baseline import evaluate_cases

            cases_path = REPO_ROOT / "tests/evals/intent_eval_cases.json"
            result = evaluate_cases(json.loads(cases_path.read_text(encoding="utf-8")))
            passed = result["macro_f1"] >= 0.85
            details = {"cases": result["cases"], "accuracy": result["accuracy"], "macro_f1": result["macro_f1"]}
        except (OSError, ValueError, ImportError) as exc:
            passed, details = False, str(exc)
        checks.append({"name": "intent macro-f1 threshold", "passed": passed, "details": details})
    elif module_id == "07_sft_and_dpo":
        requirements = REPO_ROOT / "algorithm/training/requirements-training.txt"
        configs = [REPO_ROOT / "algorithm/training/configs/sft_qwen3b.json", REPO_ROOT / "algorithm/training/configs/dpo_qwen3b.json"]
        checks.append({"name": "training dependency and configs", "passed": requirements.exists() and all(path.exists() for path in configs), "details": [str(path) for path in configs]})
    elif module_id == "08_evaluation_and_interview":
        log_dir = REPO_ROOT / "logs/experiments"
        checks.append({"name": "experiment log exists", "passed": any(log_dir.glob("*.log")), "details": str(log_dir)})

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
            print(f"- {card.module_id}: {value['status']}" + (f" · {value['evidence']}" if value.get("evidence") else ""))
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

