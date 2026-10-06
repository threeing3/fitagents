"""Run the original 18 requirement checks; passing checks are not deployment proof."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def node(module: str, name: str) -> str:
    return f"tests/test_{module}.py::test_{name}"


CASES = [
    (
        1,
        "指定日期及其他安排保留",
        [node("business_state_replay", "original_friday_chat_changes_only_requested_session")],
    ),
    (
        2,
        "询问引用否定不创建",
        [node("responsibility_chat_review", "question_example_and_negation_not_commands")],
    ),
    (
        3,
        "保存记录并禁止动作",
        [
            node(
                "workout_conversation_chain",
                "record_workout_and_block_excluded_advice_before_display",
            )
        ],
    ),
    (
        4,
        "改目标撤销旧审批",
        [
            node(
                "persistent_approvals",
                "goal_correction_eagerly_invalidates_without_deleting_history",
            )
        ],
    ),
    (
        5,
        "当前风险阻止旧任务",
        [
            node(
                "persistent_approvals",
                "worker_rechecks_dependencies_even_without_invalidation_hook",
            )
        ],
    ),
    (
        6,
        "打卡不绕过先询问",
        [
            node(
                "persistent_approvals",
                "checkin_drafts_only_then_approval_changes_one_day_and_registers_evaluation",
            )
        ],
    ),
    (
        7,
        "同日纠正排除旧状态",
        [
            node(
                "business_state_replay",
                "same_day_checkin_versions_memory_and_excludes_old_state_from_context",
            )
        ],
    ),
    (
        8,
        "省略与清空分开",
        [
            node("business_state_replay", "partial_profile_update_preserves_omitted_fields"),
            node(
                "business_state_replay", "explicit_empty_profile_lists_clear_only_selected_fields"
            ),
        ],
    ),
    (
        9,
        "批准不等于执行",
        [
            node(
                "persistent_approvals",
                "history_exposes_durable_execution_not_just_approval_and_is_owner_scoped",
            )
        ],
    ),
    (
        10,
        "响应丢失不重复写",
        [node("business_state_replay", "real_workout_commit_then_response_lost_is_not_retried")],
    ),
    (
        11,
        "同基线竞争旧写拒绝",
        [node("plan_writes", "stale_independent_session_cannot_overwrite_committed_plan")],
    ),
    (
        12,
        "暂停取消到期阻止写",
        [node("persistent_approvals", "changed_context_blocks_previously_approved_action")],
    ),
    (
        13,
        "无证据不编造失败",
        [
            node(
                "decision_evaluation",
                "due_scan_marks_expired_plan_insufficient_without_false_failure",
            )
        ],
    ),
    (
        14,
        "未采用不强行归因",
        [
            node(
                "decision_evaluation", "workout_records_do_not_prove_plan_adoption_or_strategy_gain"
            )
        ],
    ),
    (
        15,
        "拒绝追问持久停止",
        [
            node(
                "responsibility_chat_review",
                "chat_followup_refusal_persists_and_replays_without_guessing",
            ),
            node(
                "responsibility_chat_review",
                "streamed_followup_refusal_commits_same_state_and_public_events",
            ),
        ],
    ),
    (
        16,
        "时区与窗口",
        [
            node("responsibilities", "schedule_handles_dst_gap_and_ambiguous_hour"),
            node("responsibilities", "review_window_uses_local_dates_and_excludes_future_records"),
        ],
    ),
    (
        17,
        "检索注入不扩大权限",
        [
            node(
                "business_state_replay",
                "untrusted_retrieved_memory_cannot_authorize_model_plan_write",
            )
        ],
    ),
    (
        18,
        "跨用户统一隔离",
        [node("persistent_approvals", "unified_owner_isolation_read_approve_execute_and_journal")],
    ),
]


def read_results(path: Path) -> dict[str, str]:
    results = {}
    for case in ElementTree.parse(path).iter("testcase"):
        key = case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"]
        if key in results:
            raise ValueError(f"Duplicate test result: {key}")
        results[key] = (
            "failed"
            if case.find("failure") is not None or case.find("error") is not None
            else "skipped"
            if case.find("skipped") is not None
            else "passed"
        )
    return results


def evaluate(nodes: list[str], results: dict[str, str]) -> dict:
    matched = {}
    missing = []
    for prefix in nodes:
        found = {
            key: value
            for key, value in results.items()
            if key == prefix or key.startswith(prefix + "[")
        }
        if not found:
            missing.append(prefix)
        matched.update(found)
    passed = not missing and bool(matched) and all(value == "passed" for value in matched.values())
    return {"selected_checks_passed": passed, "missing": missing, "results": matched}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    nodes = list(dict.fromkeys(item for _, _, checks in CASES for item in checks))
    if args.list:
        print(json.dumps(CASES, ensure_ascii=False, indent=2))
        return 0
    folder = ROOT / "logs" / "acceptance" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True, exist_ok=False)
    xml_path = folder / "pytest.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *nodes,
        "-v",
        "--tb=short",
        "--timeout=30",
        f"--junitxml={xml_path}",
    ]
    with (folder / "steps.log").open("w", encoding="utf-8") as log:
        log.write("Selected assertion replay, not full requirement or deployment acceptance.\n")
        log.write(json.dumps(command, ensure_ascii=False) + "\n")
        log.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        code = process.wait()
    results = read_results(xml_path) if xml_path.exists() else {}
    entries = [
        {"requirement": number, "label": label, **evaluate(checks, results)}
        for number, label, checks in CASES
    ]
    report = {
        "process_exit_code": code,
        "all_requirements_proven": False,
        "scope": "Selected local assertion checks. UI, actual concurrent processes, production deployment and broader semantic coverage require separate evidence.",
        "cases": entries,
    }
    (folder / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Report: {folder / 'report.json'}")
    return code or (0 if all(entry["selected_checks_passed"] for entry in entries) else 2)


if __name__ == "__main__":
    raise SystemExit(main())
