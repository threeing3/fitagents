"""One frozen, synthetic retrieval acceptance run; no paid or business calls."""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from algorithm.inference.intent_catalog import AgentIntentCatalog  # noqa: E402
from fast_api.app.services.intent_cascade import (  # noqa: E402
    ExampleVectorIndex,
    IntentCascadePolicy,
)
from fast_api.app.services.intent_decision import IntentRouter  # noqa: E402
from fast_api.app.services.intent_route_examples import EXAMPLE_VERSION  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/intent_vector_acceptance_v1.json"
TARGETS = {"single_top3": 0.90, "multi_complete_top3": 0.75}


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def bigrams(text: str) -> Counter:
    text = normalized(text)
    return Counter(text[i : i + 2] for i in range(len(text) - 1))


def lexical_candidates(message: str, index: ExampleVectorIndex) -> list[dict]:
    query = bigrams(message)
    query_norm = math.sqrt(sum(value * value for value in query.values()))
    ranked = []
    for example in index.examples:
        vector = bigrams(example.text)
        norm = math.sqrt(sum(value * value for value in vector.values()))
        score = (
            sum(value * vector.get(key, 0) for key, value in query.items()) / (query_norm * norm)
            if query_norm and norm
            else 0
        )
        if score > 0:
            ranked.append(
                {"example_id": example.example_id, "intent": example.intent, "score": score}
            )
    return index.distinct_intents(ranked)


def audit_cases(payload: dict, index: ExampleVectorIndex) -> dict:
    cases = payload["cases"]
    ids = [case["id"] for case in cases]
    texts = [normalized(case["text"]) for case in cases]
    example_texts = {normalized(example.text) for example in index.examples}
    if len(ids) != len(set(ids)) or len(texts) != len(set(texts)):
        raise ValueError("Duplicate acceptance cases")
    if example_texts.intersection(texts):
        raise ValueError("Exact cross-split overlap")
    for case in cases:
        labels = set(case["expected"]) | set(case.get("forbidden", []))
        if not labels.issubset(AgentIntentCatalog.VALID_INTENTS):
            raise ValueError("Invalid acceptance labels")
        if set(case["expected"]) & set(case.get("forbidden", [])):
            raise ValueError("Conflicting annotations")
    near_pairs = []
    for case in cases:
        query = set(bigrams(case["text"]))
        for example in index.examples:
            other = set(bigrams(example.text))
            union = query | other
            jaccard = len(query & other) / len(union) if union else 0
            if jaccard >= 0.8:
                near_pairs.append({"case_id": case["id"], "example_id": example.example_id})
    # Reject obvious near-copies; this is a mechanical audit, not proof of independence.
    if near_pairs:
        raise ValueError(f"Near-copy examples: {near_pairs}")
    return {
        "case_count": len(cases),
        "example_count": len(index.examples),
        "groups": dict(Counter(case["group"] for case in cases)),
        "exact_overlap": 0,
        "near_pairs_at_jaccard_08": len(near_pairs),
        "reviewer": "implementation_agent_not_independent",
    }


def retrieval_metrics(candidates: list[dict], expected: list[str]) -> dict:
    retrieved = [item["intent"] for item in candidates]
    expected_set = set(expected)
    return {
        "top1_correct": bool(retrieved and retrieved[0] in expected_set),
        "complete_top3": bool(expected_set and expected_set.issubset(retrieved)),
        "task_recall_top3": len(expected_set & set(retrieved)) / len(expected_set)
        if expected_set
        else None,
    }


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(row)
    report = {}
    for group, items in groups.items():
        labelled = [item for item in items if item["expected"]]
        metrics = {}
        for method in ("rule", "lexical", "semantic"):
            metrics[method] = {
                "top1_correct": sum(row[method]["top1_correct"] for row in labelled),
                "complete_top3": sum(row[method]["complete_top3"] for row in labelled),
                "labelled_count": len(labelled),
            }
        report[group] = {"count": len(items), "metrics": metrics}
    single = report["single"]["metrics"]["semantic"]
    multi = report["multi"]["metrics"]["semantic"]
    single_rate = single["complete_top3"] / single["labelled_count"]
    multi_rate = multi["complete_top3"] / multi["labelled_count"]
    unexpected_accepts = [row["case_id"] for row in rows if row["cascade_outcome"] == "accept"]
    unavailable = [row["case_id"] for row in rows if not row["encoder_available"]]
    forbidden_retrieved = [
        row["case_id"]
        for row in rows
        if set(row["forbidden"]) & {item["intent"] for item in row["semantic_candidates"]}
    ]
    failures = [
        row["case_id"] for row in rows if row["expected"] and not row["semantic"]["complete_top3"]
    ]
    return {
        "groups": report,
        "single_top3_rate": single_rate,
        "multi_complete_top3_rate": multi_rate,
        "unexpected_accepts": unexpected_accepts,
        "encoder_unavailable": unavailable,
        "forbidden_label_in_candidates": forbidden_retrieved,
        "semantic_task_omissions": failures,
        "targets_met": single_rate >= TARGETS["single_top3"]
        and multi_rate >= TARGETS["multi_complete_top3"]
        and not unexpected_accepts
        and not unavailable,
        "claim_scope": "candidate_retrieval_and_escalation_only_not_final_task_execution",
    }


def main() -> int:
    log_dir = ROOT / "logs/experiments"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    path = log_dir / f"intent_acceptance_{stamp}.jsonl"
    with path.open("x", encoding="utf-8") as log:

        def record(item):
            log.write(json.dumps(item, ensure_ascii=False) + "\n")
            log.flush()

        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        index = ExampleVectorIndex()
        policy = IntentCascadePolicy(index)
        router = IntentRouter()
        record(
            {
                "event": "start",
                "fixture": payload["version"],
                "examples": EXAMPLE_VERSION,
                "targets": TARGETS,
                "provenance": payload["provenance"],
            }
        )
        try:
            audit = audit_cases(payload, index)
            record({"event": "split_audit", **audit})
            rows = []
            for case in payload["cases"]:
                message = case["text"]
                decision = router.analyze(message)
                started = time.perf_counter()
                cascade = policy.assess(message, decision)
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                semantic = cascade.candidates
                lexical = lexical_candidates(message, index)
                rule_intents = [decision.primary_intent, *decision.secondary_intents]
                rule = [{"intent": intent} for intent in dict.fromkeys(rule_intents)]
                # Rules have no top-3 ranking; measure their entire predicted set.
                row = {
                    "event": "case",
                    "case_id": case["id"],
                    "group": case["group"],
                    "text": message,
                    "expected": case["expected"],
                    "forbidden": case.get("forbidden", []),
                    "semantic": retrieval_metrics(semantic, case["expected"]),
                    "lexical": retrieval_metrics(lexical, case["expected"]),
                    "rule": retrieval_metrics(rule, case["expected"]),
                    "semantic_candidates": semantic,
                    "lexical_candidates": lexical,
                    "rule_intents": rule_intents,
                    "cascade_outcome": cascade.outcome,
                    "cascade_reason": cascade.reason,
                    "elapsed_ms": elapsed,
                    "encoder_available": "available;" in cascade.reason,
                }
                rows.append(row)
                record(row)
            summary = summarize(rows)
            record({"event": "summary", **summary})
            print(
                json.dumps(
                    {"log": str(path), "audit": audit, **summary}, ensure_ascii=True, indent=2
                )
            )
            return 0 if summary["targets_met"] else 2
        except Exception as exc:
            record({"event": "failed", "error_type": type(exc).__name__, "error": str(exc)})
            raise


if __name__ == "__main__":
    raise SystemExit(main())
