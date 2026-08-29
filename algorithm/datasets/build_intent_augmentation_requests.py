"""Build leakage-safe language-diversity requests without reading evaluation text."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from algorithm.data.intent_augmentation_contract import (
    VALID_LANGUAGE_FACTORS,
    IntentAugmentationRequest,
)
from algorithm.data.multilabel_intent_dataset_factory import INTENT_SEEDS, SECONDARY_COMBINATIONS


def build_requests(*, requested_source: str = "teacher_generated") -> list[dict[str, Any]]:
    factors = tuple(sorted(VALID_LANGUAGE_FACTORS))
    requests: list[IntentAugmentationRequest] = []
    for index, seed in enumerate(INTENT_SEEDS):
        for split_offset, split in enumerate(("train", "validation")):
            factor = factors[(index + split_offset) % len(factors)]
            requests.append(
                IntentAugmentationRequest(
                    request_id=f"augment-primary-{seed.intent}-{split}",
                    primary_intent=seed.intent,
                    secondary_intents=(),
                    semantic_brief=seed.calibration_message,
                    language_factor=factor,
                    requested_source=requested_source,
                    split_target=split,
                )
            )
    for index, (primary, secondary, brief) in enumerate(SECONDARY_COMBINATIONS):
        for split_offset, split in enumerate(("train", "validation")):
            factor = factors[(index + split_offset + 2) % len(factors)]
            requests.append(
                IntentAugmentationRequest(
                    request_id=f"augment-multi-{primary}-{secondary}-{split}",
                    primary_intent=primary,
                    secondary_intents=(secondary,),
                    semantic_brief=brief,
                    language_factor=factor,
                    requested_source=requested_source,
                    split_target=split,
                )
            )
    errors = [error for request in requests for error in request.validate()]
    if errors:
        raise ValueError(f"invalid augmentation requests: {errors[:5]}")
    return [request.to_dict() for request in requests]


def build_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "fitagent-intent-augmentation-request-manifest/v1",
        "request_count": len(rows),
        "split_counts": dict(Counter(row["split_target"] for row in rows)),
        "source_counts": dict(Counter(row["requested_source"] for row in rows)),
        "factor_counts": dict(Counter(row["language_factor"] for row in rows)),
        "development_text_access": any(row["development_text_access"] for row in rows),
        "fixed_test_text_access": any(row["fixed_test_text_access"] for row in rows),
        "claims": {
            "contains_generated_outputs": False,
            "safe_to_start_generation": True,
            "human_review_required_before_quality_claim": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", default="teacher_generated")
    args = parser.parse_args()
    rows = build_requests(requested_source=args.source)
    manifest = build_manifest(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
