"""CPU business-model experiment on explicitly simulated outcomes."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from algorithm.data.sample_factory import build_synthetic_examples
from algorithm.business.acceptance_model import MajorityAcceptanceModel, fit_acceptance_model
from algorithm.business.feature_builder import build_features
from algorithm.business.label_builder import build_outcome_label
from algorithm.evaluation.business_eval import mean_ndcg, probabilistic_metrics
from algorithm.evaluation.report import write_report


def run_business_baseline(count: int = 240, seed: int = 42) -> dict[str, Any]:
    examples = [row.to_dict() for row in build_synthetic_examples(count, seed)]
    rows = [
        {
            "example": example,
            "features": build_features(example),
            "label": build_outcome_label(example)["accepted"],
        }
        for example in examples
    ]
    train = [row for row in rows if row["example"].get("split") == "train"]
    test = [row for row in rows if row["example"].get("split") == "test"]
    if not train or not test:
        raise ValueError("simulation must produce non-empty train and test user groups")
    train_features = [row["features"] for row in train]
    train_labels = [row["label"] for row in train]
    test_features = [row["features"] for row in test]
    test_labels = [int(row["label"]) for row in test]

    majority = MajorityAcceptanceModel().fit(train_features, train_labels)
    majority_probabilities = majority.predict_proba(test_features)
    model = fit_acceptance_model(train_features, train_labels)
    model_probabilities = model.predict_proba(test_features)

    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for row, probability in zip(test, model_probabilities):
        grouped[str(row["example"].get("user_hash"))].append((probability, int(row["label"])))
    ranking_records = []
    for values in grouped.values():
        ranked = sorted(values, key=lambda item: item[0], reverse=True)
        ranking_records.append({"relevances": [label for _, label in ranked]})

    return {
        "dataset": {
            "total": len(rows),
            "train": len(train),
            "test": len(test),
            "user_count": len({row["example"].get("user_hash") for row in rows}),
            "source_counts": dict(Counter(str(row["example"].get("source")) for row in rows)),
            "label_source_counts": dict(Counter(str((row["example"].get("outcome") or {}).get("label_source")) for row in rows)),
        },
        "majority_baseline": probabilistic_metrics(test_labels, majority_probabilities),
        "acceptance_model": {
            "class": type(model).__name__,
            "metrics": probabilistic_metrics(test_labels, model_probabilities),
        },
        "ranking": {"mean_ndcg_at_5": mean_ndcg(ranking_records, 5), "groups": len(ranking_records)},
        "notes": ["simulated_outcome only; do not present as online business lift", "split is user-disjoint"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a CPU acceptance model on simulated outcomes")
    parser.add_argument("--count", type=int, default=240)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    metrics = run_business_baseline(args.count, args.seed)
    notes = list(metrics.get("notes") or [])
    write_report(args.output, args.experiment_id, metrics, notes)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
