from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MemorySearchSpec:
    label: str
    rationale: str
    top_k: int = 4
    category: str | None = None
    memory_network: str | None = None
    fact_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryRecallPlan:
    intent: str
    category: str | None
    top_k: int
    searches: list[MemorySearchSpec]
    excluded_networks: list[str]
    rationale: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "category": self.category,
            "top_k": self.top_k,
            "searches": [search.to_dict() for search in self.searches],
            "excluded_networks": self.excluded_networks,
            "rationale": self.rationale,
        }


class MemoryPlanner:
    """Plan memory recall by task type before ContextBuilder performs retrieval."""

    def build_plan(
        self,
        intent: str,
        query: str,
        category: str | None,
        core_profile: dict[str, Any] | None = None,
        active_risk_notes: list[dict[str, Any]] | None = None,
    ) -> MemoryRecallPlan:
        lowered = (query or "").lower()
        risk_present = bool(active_risk_notes) or any(term in lowered for term in ["pain", "injury", "thyroid", "dizzy", "疼", "痛", "甲亢"])
        if intent == "injury_or_risk":
            return self._risk_plan(intent, category)
        if intent in {"training_plan", "progression_decision", "recovery_check"}:
            return self._training_plan(intent, category, risk_present)
        if intent in {"nutrition_advice", "nutrition_log"}:
            return self._nutrition_plan(intent, category)
        if intent in {"weekly_review", "monthly_review"}:
            return self._review_plan(intent, category)
        if intent == "memory_query":
            return self._memory_query_plan(intent, category)
        return self._general_plan(intent, category)

    def _risk_plan(self, intent: str, category: str | None) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("risk_facts", "Risk questions need factual health and symptom memories first.", 5, category="risk", memory_network="world"),
            MemorySearchSpec("risk_observations", "Observed symptom patterns can guide conservative coaching.", 3, category="risk", memory_network="observation"),
            MemorySearchSpec("risk_successful_strategies", "Prior outcome-backed strategies can guide safe substitutions.", 3, category="training", memory_network="experience", fact_kind="strategy_experience"),
            MemorySearchSpec("risk_failed_strategies", "Failed strategies should be visible so they are not repeated.", 3, category="training", memory_network="experience", fact_kind="failed_strategy"),
        ]
        return MemoryRecallPlan(intent, "risk", 8, searches, ["opinion"], ["Risk recall protects facts, observations, and outcome evidence before opinions."])

    def _training_plan(self, intent: str, category: str | None, risk_present: bool) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("training_facts", "Training plans need broad user facts and current constraints.", 5, category=category, memory_network="world"),
            MemorySearchSpec("successful_strategies", "Outcome-backed successful strategies should be reused when similar.", 4, category="training", memory_network="experience", fact_kind="strategy_experience"),
            MemorySearchSpec("failed_strategies", "Previously failed strategies should be recalled so they can be avoided.", 3, category="training", memory_network="experience", fact_kind="failed_strategy"),
            MemorySearchSpec("training_observations", "Observed training/recovery patterns help adapt the plan.", 3, category="training", memory_network="observation"),
        ]
        if risk_present:
            searches.insert(1, MemorySearchSpec("risk_facts", "Risk terms in the current request require risk facts even for training plans.", 4, category="risk", memory_network="world"))
        return MemoryRecallPlan(intent, category, 8, searches, ["opinion"], ["Training recall now prioritizes facts plus outcome-backed experience over unsupported opinions."])

    def _nutrition_plan(self, intent: str, category: str | None) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("nutrition_facts", "Nutrition advice needs user habits and constraints.", 5, category="nutrition", memory_network="world"),
            MemorySearchSpec("nutrition_successful_strategies", "Reuse nutrition strategies that improved adherence or targets.", 4, category="nutrition", memory_network="experience", fact_kind="strategy_experience"),
            MemorySearchSpec("nutrition_failed_strategies", "Recall failed nutrition strategies to avoid repeating them.", 3, category="nutrition", memory_network="experience", fact_kind="failed_strategy"),
            MemorySearchSpec("nutrition_observations", "Nutrition observations summarize adherence patterns.", 3, category="nutrition", memory_network="observation"),
        ]
        return MemoryRecallPlan(intent, "nutrition", 8, searches, ["opinion"], ["Nutrition recall emphasizes adherence outcomes and user constraints."])

    def _review_plan(self, intent: str, category: str | None) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("recent_observations", "Reviews need observation memories first.", 5, memory_network="observation"),
            MemorySearchSpec("successful_strategies", "Reviews should surface what worked.", 4, memory_network="experience", fact_kind="strategy_experience"),
            MemorySearchSpec("failed_strategies", "Reviews should surface what did not work.", 4, memory_network="experience", fact_kind="failed_strategy"),
            MemorySearchSpec("facts", "Stable facts keep review conclusions grounded.", 4, memory_network="world"),
        ]
        return MemoryRecallPlan(intent, None, 8, searches, [], ["Review recall includes facts, observations, and outcome-backed experiences."])

    def _memory_query_plan(self, intent: str, category: str | None) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("all_facts", "Memory queries should allow broad fact recall.", 5, memory_network="world"),
            MemorySearchSpec("all_experience", "Memory queries can expose past decisions and outcomes.", 4, memory_network="experience"),
            MemorySearchSpec("all_observations", "Memory queries can include summarized patterns.", 4, memory_network="observation"),
            MemorySearchSpec("opinion_with_evidence", "Opinions are allowed only as explicitly marked judgments.", 2, memory_network="opinion"),
        ]
        return MemoryRecallPlan(intent, None, 8, searches, [], ["Memory queries intentionally expose all memory networks with explicit labels."])

    def _general_plan(self, intent: str, category: str | None) -> MemoryRecallPlan:
        searches = [
            MemorySearchSpec("general_facts", "General chat should use stable facts lightly.", 4, category=category, memory_network="world"),
            MemorySearchSpec("general_observations", "General chat can use observations as background patterns.", 2, category=category, memory_network="observation"),
        ]
        return MemoryRecallPlan(intent, category, 6, searches, ["opinion"], ["General recall stays compact and avoids unsupported opinions."])

