"""Conservative early-exit policy; similarity is evidence, not authority."""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field

from algorithm.inference.intent_catalog import AgentIntentCatalog
from fast_api.app.services.intent_decision import IntentDecision
from fast_api.app.services.intent_embeddings import LOCAL_INTENT_ENCODER
from fast_api.app.services.intent_route_examples import EXAMPLE_TEXTS, EXAMPLE_VERSION


@dataclass(frozen=True)
class RouteExample:
    text: str
    intent: str
    example_id: str


@dataclass
class CascadeAssessment:
    outcome: str
    source: str
    reason: str
    candidates: list[dict] = field(default_factory=list)
    intent: str | None = None

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "source": self.source,
            "reason": self.reason,
            "candidates": self.candidates,
            "dispatch_intent": self.intent,
            "policy_version": "intent_cascade_v2",
        }


class ExampleVectorIndex:
    """Real semantic cosine retrieval, deliberately without acceptance authority."""

    def __init__(self, examples: list[RouteExample] | None = None, encoder=None):
        self.encoder = encoder or LOCAL_INTENT_ENCODER
        self._vectors = None
        self._lock = threading.Lock()
        self.examples = (
            examples
            if examples is not None
            else [
                RouteExample(text, intent, f"{intent}-{number:02}")
                for intent, texts in EXAMPLE_TEXTS.items()
                for number, text in enumerate(texts, start=1)
            ]
        )
        if any(example.intent not in AgentIntentCatalog.VALID_INTENTS for example in self.examples):
            raise ValueError("Unknown example intent")

    @staticmethod
    def _normalize(vector) -> list[float]:
        values = list(vector)
        if not values or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("Invalid vector")
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("Zero or invalid vector norm")
        return [value / norm for value in values]

    def search(self, message: str, limit: int = 3) -> list[dict]:
        return self.retrieve(message, limit)[0]

    def retrieve(self, message: str, limit: int = 3) -> tuple[list[dict], str]:
        if not message.strip() or not self.examples or limit <= 0:
            return [], "empty_input_or_index"
        if len(message) > 2000:
            return [], "input_too_long"
        try:
            with self._lock:
                if self._vectors is None:
                    encoded = self.encoder.encode([example.text for example in self.examples])
                    if len(encoded) != len(self.examples):
                        raise ValueError("Wrong vector count")
                    vectors = [self._normalize(item) for item in encoded]
                    if len({len(item) for item in vectors}) != 1:
                        raise ValueError("Inconsistent dimensions")
                    self._vectors = vectors
                encoded = self.encoder.encode([message])
                if len(encoded) != 1:
                    raise ValueError("Wrong query count")
                vector = self._normalize(encoded[0])
                if len(vector) != len(self._vectors[0]):
                    raise ValueError("Query dimension mismatch")
        except Exception:
            # No raw exception text: tokenizer paths or input could be private.
            return [], "semantic_encoder_unavailable_or_invalid"
        ranked = []
        for example, other in zip(self.examples, self._vectors, strict=True):
            score = max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, other, strict=True))))
            ranked.append(
                {
                    "example_id": example.example_id,
                    "intent": example.intent,
                    "score": score,
                    "encoder": self.encoder.model_id,
                    "score_type": "semantic_cosine",
                    "example_version": EXAMPLE_VERSION,
                }
            )
        return self.distinct_intents(ranked, limit), "available"

    @staticmethod
    def distinct_intents(ranked: list[dict], limit: int = 3) -> list[dict]:
        """Keep the best example per intent, without losing other task candidates."""
        selected = []
        seen = set()
        for item in sorted(ranked, key=lambda item: (-item["score"], item["example_id"])):
            if item["intent"] not in seen:
                selected.append(item)
                seen.add(item["intent"])
            if len(selected) >= limit:
                break
        return selected


class IntentCascadePolicy:
    """Only full-message allowlisted requests may skip semantic refinement."""

    SIMPLE_PATTERNS = {
        "general_chat": r"(?:你好|您好|谢谢|早上好|晚上好|hello|hi|thanks)",
        "training_plan": r"(?:帮我|请)?(?:制定|生成)(?:一个|一份)?(?:一周|本周|下周)?训练计划",
        "memory_query": r"(?:帮我|请)?(?:查询|查看)(?:今天|昨天|上周|本周)的训练记录",
    }

    def __init__(self, index: ExampleVectorIndex | None = None):
        self.index = index or ExampleVectorIndex()

    def assess(self, message: str, decision: IntentDecision) -> CascadeAssessment:
        text = message.strip().lower().rstrip("。！？!? ")
        pattern = self.SIMPLE_PATTERNS.get(decision.primary_intent)
        if (
            pattern
            and re.fullmatch(pattern, text)
            and not decision.secondary_intents
            and decision.risk_level == "low"
        ):
            # Intent completeness and execution readiness are different checks.
            return CascadeAssessment("accept", "rule", "full_message_allowlist")
        candidates, status = self.index.retrieve(message)
        return CascadeAssessment(
            "escalate",
            "example_vector",
            f"retrieval_only_uncalibrated; {status}; semantic_review_required",
            candidates,
        )


DEFAULT_SEMANTIC_INDEX = ExampleVectorIndex()
