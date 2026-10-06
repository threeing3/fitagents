"""Frozen synthetic memory-use probes; preparation/scoring never calls a model."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from fast_api.app.services.coaching_prompt_payload import serialize_coaching_prompt

REVISION = "memory-use-v1"
VARIANTS = ("compact_full", "referenced")
SYSTEM = (
    "根据用户问题和记忆回答，不猜测缺失信息。只输出JSON对象，包含facts。"
    "facts的键使用问题指定字段，每个值包含value字符串及source_ids字符串数组。"
    "source_ids必须指向支持该值的记忆id；没有依据时value为未提供，source_ids为空。"
    "已撤回、已更正的旧事实不能作为当前答案。遵守问题指定的场景约束。"
)


def record(key, content, *, status="active", category="training"):
    return {
        "id": key,
        "summary": content,
        "content": content,
        "status": status,
        "category": category,
        "evidence": {"source": "synthetic_user_statement", "statement": content},
    }


def fact(value, *sources):
    return {"value": value, "source_ids": list(sources)}


def cases():
    """Gold labels are evaluator-owned and excluded from all model prompts."""
    definitions = [
        (
            "correction",
            "用户更正",
            "回答current_weight，不要使用已更正体重。",
            [
                record(
                    "c-old", "我之前说体重80公斤，后来确认称重读数看错了。", status="superseded"
                ),
                record("c-new", "我更正当前体重为76公斤；之前的80公斤不是当前体重。"),
            ],
            {"current_weight": fact("76公斤", "c-new")},
            [],
        ),
        (
            "date_scope",
            "历史与当前冲突",
            "回答today_activity和yesterday_activity。",
            [
                record("d-old", "昨天完成跑步30分钟，这是历史记录，不是今天的训练。"),
                record("d-now", "今天完成骑车20分钟；请与昨天的跑步记录分开。"),
            ],
            {
                "today_activity": fact("骑车20分钟", "d-now"),
                "yesterday_activity": fact("跑步30分钟", "d-old"),
            },
            [],
        ),
        (
            "risk_constraint",
            "风险约束",
            "只选场景允许的活动，回答allowed_activity。",
            [
                record("r-pref", "我喜欢跑步和跳绳，但喜好不代表本次活动被允许。"),
                record(
                    "r-rule",
                    "本合成场景规则：本次只能选择散步，禁止跑步和跳绳。这是评测约束而非医学建议。",
                    category="risk",
                ),
            ],
            {"allowed_activity": fact("散步", "r-rule")},
            ["allowed_activity"],
        ),
        (
            "two_goals",
            "多个任务目标",
            "回答long_term_goal和today_request，不能混为一项。",
            [
                record("g-long", "我的长期目标是增肌，目标的时间范围是未来数月。"),
                record("g-now", "我今天只想记录睡眠，不要求更改增肌计划。"),
            ],
            {"long_term_goal": fact("增肌", "g-long"), "today_request": fact("记录睡眠", "g-now")},
            [],
        ),
        (
            "unknown",
            "缺失信息",
            "回答height和weight，不要用体重猜测身高。",
            [record("u-weight", "我体重70公斤。没有提供身高，也没有授权估计身高。")],
            {"height": fact("未提供"), "weight": fact("70公斤", "u-weight")},
            [],
        ),
        (
            "ownership",
            "证据归属",
            "回答user_goal，不要把朋友目标记成用户目标。",
            [
                record("o-friend", "朋友想减脂，我只是转述朋友的目标。"),
                record("o-self", "我自己的目标是提升耐力，不是朋友的减脂目标。"),
            ],
            {"user_goal": fact("提升耐力", "o-self")},
            [],
        ),
        (
            "withdrawn",
            "撤回事实",
            "回答current_injury，仅依据用户当前确认的信息。",
            [
                record("w-old", "膝伤描述已被用户撤回，不能当成当前事实。", status="retracted"),
                record("w-new", "我撤回先前的膝伤描述，没有确认任何当前伤病。"),
            ],
            {"current_injury": fact("未提供")},
            [],
        ),
        (
            "tail_constraint",
            "正文末尾约束",
            "回答duration_limit，使用当前完整规则。",
            [
                record(
                    "t-rule",
                    "我的计划记录包含热身、正式训练、整理活动和休息说明。"
                    "阅读记录时请保留各阶段的顺序，不要把热身时间视为正式训练时间。"
                    "此前的较长安排只是候选方案，并不是已经确认的执行上限。"
                    "记录最后确认：本次总时长上限为15分钟，前文候选方案不得覆盖该约束。",
                )
            ],
            {"duration_limit": fact("15分钟", "t-rule")},
            [],
        ),
    ]
    result = []
    for case_id, family, question, memories, expected, critical in definitions:
        result.append(
            {
                "case_id": case_id,
                "family": family,
                "expected": expected,
                "critical_fields": critical,
                "payload": {
                    "user_message": question,
                    "context_packet": {
                        "relevant_memories": memories,
                        "world_memories": deepcopy(memories),
                        "observation_memories": deepcopy(memories),
                    },
                },
            }
        )
    return result


def prepare(*, schema_example=False):
    rows = []
    for case in cases():
        baseline = json.dumps(case["payload"], ensure_ascii=False, separators=(",", ":"))
        for variant in VARIANTS:
            prompt = (
                baseline
                if variant == "compact_full"
                else serialize_coaching_prompt(case["payload"])
            )
            rows.append(
                {
                    "case_id": case["case_id"],
                    "variant": variant,
                    "revision": REVISION + "-schema" if schema_example else REVISION,
                    "system": SYSTEM
                    + (
                        "\n输出结构示例（字段与内容仅示意）："
                        '{"facts":{"问题指定字段":{"value":"字段答案","source_ids":["来源id"]}}}。'
                        "必须保留facts外层，仅回答问题指定字段。"
                        if schema_example
                        else ""
                    ),
                    "user": prompt,
                    "input_bytes": len((SYSTEM + prompt).encode("utf-8")),
                    "representation_changed": prompt != baseline,
                }
            )
    return rows


def score(predictions, *, revision=REVISION):
    if revision not in {REVISION, REVISION + "-schema"}:
        raise ValueError("Unknown evaluator revision")
    expected_keys = {(case["case_id"], variant) for case in cases() for variant in VARIANTS}
    indexed = {}
    for row in predictions:
        key = (row["case_id"], row["variant"])
        if key not in expected_keys or key in indexed or row.get("revision") != revision:
            raise ValueError("Unknown, duplicate, or wrong-revision prediction")
        indexed[key] = row
    if set(indexed) != expected_keys:
        raise ValueError("Missing predictions; incomplete runs must not receive a complete score")
    results = []
    for case in cases():
        for variant in VARIANTS:
            row = indexed[(case["case_id"], variant)]
            try:
                parsed = json.loads(row["response"])
                facts = parsed["facts"]
                valid = isinstance(facts, dict) and set(facts) == set(case["expected"])
                for value in facts.values():
                    valid = (
                        valid and isinstance(value, dict) and set(value) == {"value", "source_ids"}
                    )
                    valid = valid and isinstance(value["value"], str)
                    sources = value["source_ids"]
                    valid = (
                        valid
                        and isinstance(sources, list)
                        and all(isinstance(s, str) for s in sources)
                    )
                    valid = valid and len(sources) == len(set(sources))
            except (ValueError, KeyError, TypeError, AttributeError):
                valid, facts = False, {}
            values_ok = valid and all(
                facts[k]["value"] == v["value"] for k, v in case["expected"].items()
            )
            sources_ok = valid and all(
                set(facts[k]["source_ids"]) == set(v["source_ids"])
                for k, v in case["expected"].items()
            )
            critical_ok = valid and all(
                facts[k]["value"] == case["expected"][k]["value"] for k in case["critical_fields"]
            )
            results.append(
                {
                    "case_id": case["case_id"],
                    "variant": variant,
                    "format_valid": bool(valid),
                    "values_correct": bool(values_ok),
                    "sources_correct": bool(sources_ok),
                    "critical_correct": bool(critical_ok) if case["critical_fields"] else None,
                    "exact": bool(values_ok and sources_ok),
                }
            )
    summary = {}
    for variant in VARIANTS:
        selected = [r for r in results if r["variant"] == variant]
        summary[variant] = {"total": len(selected), "exact": sum(r["exact"] for r in selected)}
    paired = {"recovered": [], "regressed": [], "both_correct": [], "both_wrong": []}
    for case in cases():
        pair = [r["exact"] for r in results if r["case_id"] == case["case_id"]]
        outcome = (
            "both_correct"
            if all(pair)
            else "both_wrong"
            if not any(pair)
            else "recovered"
            if pair[1]
            else "regressed"
        )
        paired[outcome].append(case["case_id"])
    return {
        "revision": revision,
        "summary": summary,
        "cases": results,
        "paired": paired,
        "scope": "Synthetic structured memory-use probes, not business task success",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--schema-example", action="store_true")
    args = parser.parse_args()
    if args.predictions:
        rows = [
            json.loads(line)
            for line in args.predictions.read_text(encoding="utf-8").splitlines()
            if line
        ]
        revision = REVISION + "-schema" if args.schema_example else REVISION
        print(json.dumps(score(rows, revision=revision), ensure_ascii=False, indent=2))
    else:
        for row in prepare(schema_example=args.schema_example):
            print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
