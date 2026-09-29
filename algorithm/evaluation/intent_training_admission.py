"""Audit synthetic intent candidates before quality-oriented model training."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from algorithm.inference.intent_catalog import AgentIntentCatalog

PARTITION_SUFFIX = re.compile(
    r"(?:_train_\d+|_train|_validation|_calibration|-(?:train|validation))$"
)
INSTRUCTION_FRAGMENTS = (
    "请直接判断",
    "以当前信息为准",
    "不要补充未提供的信息",
    "先识别我的需求",
)


def base_family(family: str) -> str:
    """Remove only a terminal split marker; this is not a semantic similarity test."""

    return PARTITION_SUFFIX.sub("", family)


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return structural findings and explicit evidence limits without changing rows."""

    valid_intents = AgentIntentCatalog.VALID_INTENTS
    source_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    eligible_review_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    secondary_counts: Counter[str] = Counter()
    messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identifiers: Counter[str] = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    missing_fields: Counter[str] = Counter()
    invalid_labels: Counter[str] = Counter()
    parse_errors = 0
    primary_mismatches = 0
    instruction_rows = 0
    eligible_count = 0
    eligible_train_count = 0
    approved_train_count = 0
    eligible_validation_count = 0
    approved_validation_count = 0
    eligible_ids_by_split: dict[str, list[str]] = {"train": [], "validation": []}
    invalid_split_rows = 0
    for row in rows:
        for field in (
            "example_id",
            "user_message",
            "assistant_response",
            "source",
            "split",
            "template_family",
            "human_review_status",
        ):
            if not row.get(field):
                missing_fields[field] += 1
        identifier = str(row.get("example_id", ""))
        identifiers[identifier] += 1
        source = str(row.get("source", ""))
        split = str(row.get("split", ""))
        review = str(row.get("human_review_status", ""))
        source_counts[source] += 1
        split_counts[split] += 1
        invalid_split_rows += split not in eligible_ids_by_split
        review_counts[review] += 1
        eligible = row.get("training_eligible") is True
        if eligible:
            eligible_count += 1
            eligible_review_counts[review] += 1
            if split == "train":
                eligible_train_count += 1
                approved_train_count += review == "approved"
            elif split == "validation":
                eligible_validation_count += 1
                approved_validation_count += review == "approved"
            if split in eligible_ids_by_split:
                eligible_ids_by_split[split].append(identifier)
        message = str(row.get("user_message", ""))
        messages[message].append(row)
        if any(fragment in message for fragment in INSTRUCTION_FRAGMENTS):
            instruction_rows += 1
        family = base_family(str(row.get("template_family", "")))
        families[family].add(split)
        try:
            decision = row.get("assistant_response", {})
            if isinstance(decision, str):
                decision = json.loads(decision)
            if not isinstance(decision, dict):
                raise ValueError("decision is not an object")
            primary = str(decision.get("primary_intent", ""))
            secondary = decision.get("secondary_intents", [])
            if not isinstance(secondary, list):
                raise ValueError("secondary_intents is not a list")
            primary_counts[primary] += 1
            secondary_counts.update(str(label) for label in secondary)
            invalid_labels.update(
                label for label in [primary, *secondary] if label not in valid_intents
            )
            primary_mismatches += primary != row.get("intent_label")
        except (ValueError, TypeError, json.JSONDecodeError):
            parse_errors += 1

    duplicated_messages = {text: items for text, items in messages.items() if len(items) > 1}
    cross_split_messages = [
        text
        for text, items in messages.items()
        if len({str(item.get("split")) for item in items}) > 1
    ]
    shared_base_families = sorted(
        family
        for family, splits in families.items()
        if "train" in splits and "validation" in splits
    )
    duplicate_ids = sum(count - 1 for count in identifiers.values() if count > 1)
    structure_ok = not (
        duplicate_ids
        or cross_split_messages
        or missing_fields
        or parse_errors
        or invalid_labels
        or primary_mismatches
        or invalid_split_rows
    )
    train_reviewed = eligible_train_count > 0 and approved_train_count == eligible_train_count
    validation_reviewed = (
        eligible_validation_count > 0 and approved_validation_count == eligible_validation_count
    )
    # A split-coded template name alone never proves independent scenario families.
    scenario_evidence_present = (
        all(
            bool(row.get("scenario_group_id"))
            for row in rows
            if row.get("training_eligible") is True
        )
        and eligible_count > 0
    )
    scenario_groups: dict[str, set[str]] = defaultdict(set)
    if scenario_evidence_present:
        for row in rows:
            if row.get("training_eligible") is True:
                scenario_groups[str(row["scenario_group_id"])].add(str(row.get("split")))
    cross_split_scenario_groups = sorted(
        group for group, splits in scenario_groups.items() if len(splits) > 1
    )
    scenario_split_verified = scenario_evidence_present and not cross_split_scenario_groups
    return {
        "schema_version": "fitagent-intent-training-admission/v2",
        "grain": "one candidate row per example_id; text may repeat",
        "rows": len(rows),
        "source_counts": dict(sorted(source_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "review_counts": dict(sorted(review_counts.items())),
        "eligible_rows": eligible_count,
        "eligible_review_counts": dict(sorted(eligible_review_counts.items())),
        "eligible_train_rows": eligible_train_count,
        "approved_eligible_train_rows": approved_train_count,
        "eligible_validation_rows": eligible_validation_count,
        "approved_eligible_validation_rows": approved_validation_count,
        "eligible_example_ids_by_split": {
            split: sorted(ids) for split, ids in eligible_ids_by_split.items()
        },
        "unique_messages": len(messages),
        "duplicate_message_groups": len(duplicated_messages),
        "rows_in_duplicate_message_groups": sum(map(len, duplicated_messages.values())),
        "duplicate_example_ids": duplicate_ids,
        "cross_split_exact_message_groups": len(cross_split_messages),
        "instruction_style_rows": instruction_rows,
        "missing_required_fields": dict(sorted(missing_fields.items())),
        "decision_parse_errors": parse_errors,
        "invalid_intent_labels": dict(sorted(invalid_labels.items())),
        "outer_primary_mismatches": primary_mismatches,
        "invalid_split_rows": invalid_split_rows,
        "primary_counts": dict(sorted(primary_counts.items())),
        "secondary_counts": dict(sorted(secondary_counts.items())),
        "shared_base_template_families": len(shared_base_families),
        "shared_base_template_family_examples": shared_base_families[:10],
        "scenario_group_id_present_for_eligible": scenario_evidence_present,
        "cross_split_scenario_groups": cross_split_scenario_groups[:10],
        "structural_checks_pass": structure_ok,
        "human_review_complete_for_train": train_reviewed,
        "human_review_complete_for_validation": validation_reviewed,
        "scenario_split_verified": scenario_split_verified,
        "quality_training_admitted": (
            structure_ok and train_reviewed and validation_reviewed and scenario_split_verified
        ),
        "interpretation": (
            "Shared base template families flag possible overlap, not proven paraphrase leakage. "
            "Structural checks do not validate label semantics or action permissions."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    rows = [
        json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line
    ]
    report = audit_rows(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
