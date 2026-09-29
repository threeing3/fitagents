"""Curate teacher paraphrases and build the second-round multi-intent dataset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.deduplicate import normalize_text
from algorithm.data.intent_augmentation_contract import IntentAugmentationOutput
from algorithm.data.multi_secondary_intent_factory import build_multi_secondary_examples
from algorithm.data.schemas import TrainingExample, stable_hash
from algorithm.data.validate_dataset import read_jsonl, validate_training_rows
from algorithm.inference.intent_catalog import AgentIntentCatalog

FORBIDDEN_OUTPUT_MARKERS = (
    "主意图：",
    "次意图：",
    "语义简述：",
    '"messages"',
    "```",
    "评测集",
)


def _decision(primary: str, secondary: list[str]) -> str:
    return json.dumps(
        {
            "primary_intent": primary,
            "secondary_intents": secondary,
            "risk_level": "high" if primary == "injury_or_risk" else "low",
            "needs_clarification": False,
            "reason_codes": ["ontology_v3", "teacher_paraphrase"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _request_map(requests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for request in requests:
        request_id = str(request.get("request_id") or "")
        if not request_id:
            raise ValueError("augmentation request is missing request_id")
        if request_id in result:
            raise ValueError(f"duplicate augmentation request: {request_id}")
        result[request_id] = request
    return result


def _teacher_errors(
    output: dict[str, Any], request: dict[str, Any] | None, excluded_request_ids: set[str]
) -> list[str]:
    request_id = str(output.get("request_id") or "")
    errors: list[str] = []
    if request is None:
        return ["unknown_request"]
    if request_id in excluded_request_ids:
        errors.append("curation_exclusion")
    contract = IntentAugmentationOutput(
        request_id=request_id,
        user_message=str(output.get("user_message") or ""),
        primary_intent=str(output.get("primary_intent") or ""),
        secondary_intents=tuple(str(item) for item in output.get("secondary_intents", [])),
        source=str(output.get("source") or ""),
        generator_id=str(output.get("generator_id") or ""),
        prompt_version=str(output.get("prompt_version") or ""),
        human_review_status=str(output.get("human_review_status") or "pending"),
    )
    if contract.validate():
        errors.append("invalid_output_contract")
    if output.get("primary_intent") != request.get("primary_intent"):
        errors.append("primary_label_mismatch")
    if list(output.get("secondary_intents", [])) != list(request.get("secondary_intents", [])):
        errors.append("secondary_label_mismatch")
    if output.get("split_target") != request.get("split_target"):
        errors.append("split_mismatch")
    if output.get("language_factor") != request.get("language_factor"):
        errors.append("language_factor_mismatch")
    labels = {
        str(output.get("primary_intent") or ""),
        *map(str, output.get("secondary_intents", [])),
    }
    if not labels <= AgentIntentCatalog.VALID_INTENTS:
        errors.append("unknown_runtime_label")
    message = str(output.get("user_message") or "").strip()
    if not 4 <= len(message) <= 160:
        errors.append("message_length")
    if any(marker in message for marker in FORBIDDEN_OUTPUT_MARKERS):
        errors.append("prompt_or_eval_marker")
    return errors


def build_dataset(
    base_rows: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    teacher_rows: list[dict[str, Any]],
    *,
    excluded_request_ids: set[str] | None = None,
    multi_secondary_rows: list[dict[str, Any]] | None = None,
    dataset_version: str = "intent-multilabel-v3-20260922",
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return canonical rows plus manifest and candidate-level audit."""

    excluded_request_ids = excluded_request_ids or set()
    multi_secondary_rows = multi_secondary_rows or []
    requests_by_id = _request_map(requests)
    seen_messages = {normalize_text(str(row["user_message"])) for row in base_rows}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    request_positions: Counter[str] = Counter()

    for output in teacher_rows:
        request_id = str(output.get("request_id") or "")
        request = requests_by_id.get(request_id)
        errors = _teacher_errors(output, request, excluded_request_ids)
        normalized = normalize_text(str(output.get("user_message") or ""))
        if normalized in seen_messages:
            errors.append("exact_duplicate")
        if errors:
            rejected.append(
                {
                    "request_id": request_id,
                    "user_message": str(output.get("user_message") or ""),
                    "reasons": sorted(set(errors)),
                }
            )
            continue
        assert request is not None
        seen_messages.add(normalized)
        position = request_positions[request_id]
        request_positions[request_id] += 1
        primary = str(output["primary_intent"])
        secondary = [str(item) for item in output.get("secondary_intents", [])]
        family = f"teacher_{request_id}"
        example = TrainingExample(
            example_id=f"intent-multilabel-v3-{request_id}-{position:02d}",
            task_type="intent_decision_v2",
            user_message=str(output["user_message"]).strip(),
            user_hash=stable_hash(request_id, "intent-multilabel-v3-user"),
            session_hash=stable_hash(f"{request_id}:{position}", "intent-multilabel-v3-session"),
            assistant_response=_decision(primary, secondary),
            intent_label=primary,
            risk_label="high" if primary == "injury_or_risk" else "low",
            quality_labels={
                "weakness": "language_diversity",
                "review_status": "automated_contract_pass",
                "language_factor": str(output["language_factor"]),
            },
            label_source="teacher_label_preserved",
            template_family=family,
            teacher_model=str(output["generator_id"]),
            teacher_prompt_version=str(output["prompt_version"]),
            human_review_status="pending",
            training_eligible=True,
            model_version="none",
            prompt_version="intent-multilabel-v3",
            rule_version="intent-ontology-v3",
            source="teacher_generated",
            split=str(output["split_target"]),
            created_at="2026-09-22T00:00:00+08:00",
        )
        accepted.append(example.to_dict())

    rows = [*base_rows, *multi_secondary_rows, *accepted]
    validation = validate_training_rows(rows)
    if validation["error_count"]:
        raise ValueError(f"combined dataset validation failed: {validation['errors'][:5]}")

    rejection_counts = Counter(reason for item in rejected for reason in item["reasons"])
    audit = {
        "schema_version": "fitagent-intent-teacher-audit/v1",
        "candidate_rows": len(teacher_rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "excluded_request_ids": sorted(excluded_request_ids),
        "human_reviewed_rows": 0,
        "review_scope": "automated contract, provenance, label, split, marker, length and exact-duplicate checks",
        "rejected": rejected,
    }
    manifest = {
        "schema_version": "fitagent-intent-multilabel-manifest/v3",
        "dataset_name": "fitagent_intent_multilabel",
        "dataset_version": dataset_version,
        "row_count": len(rows),
        "base_rows": len(base_rows),
        "multi_secondary_rows": len(multi_secondary_rows),
        "teacher_rows": len(accepted),
        "split_counts": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
        "source_counts": dict(sorted(Counter(str(row["source"]) for row in rows).items())),
        "human_approved": sum(row.get("human_review_status") == "approved" for row in rows),
        "automated_teacher_acceptance": len(accepted),
        "template_family_split_leaks": validation["template_family_split_leaks"],
        "user_split_leaks": validation["user_split_leaks"],
        "claims": {
            "real_user_data": False,
            "expert_labeled": False,
            "development_used_for_generation": False,
            "fixed_test_used": False,
            "sufficient_for_pipeline_validation": True,
            "sufficient_for_model_quality_claim": False,
            "teacher_semantics_human_verified": False,
        },
    }
    return rows, manifest, audit


def _read_exclusions(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(item) for item in payload.get("excluded_request_ids", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--teacher", type=Path, action="append", required=True)
    parser.add_argument("--curation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--include-multi-secondary", action="store_true")
    parser.add_argument("--dataset-version", default="intent-multilabel-v3-20260922")
    args = parser.parse_args()
    rows, manifest, audit = build_dataset(
        read_jsonl(args.base),
        read_jsonl(args.requests),
        [row for path in args.teacher for row in read_jsonl(path)],
        excluded_request_ids=_read_exclusions(args.curation),
        multi_secondary_rows=(
            [row.to_dict() for row in build_multi_secondary_examples()]
            if args.include_multi_secondary
            else []
        ),
        dataset_version=args.dataset_version,
    )
    for path in (args.output, args.manifest, args.audit):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"manifest": manifest, "audit": {**audit, "rejected": []}}, ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
