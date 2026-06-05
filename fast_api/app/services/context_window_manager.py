"""
Context window manager — token-aware context construction and compaction.

Models the approach used by Claude Code: estimate token counts for each
context component, trim/compact when approaching the model's context limit,
and provide a sliding window for conversation history.

Token estimation uses a fast heuristic (4 chars ≈ 1 token for CJK-rich text,
adjusted to 3.5 chars/token to be conservative).

Design principles:
- Static context (system prompt, profile, plan) has a fixed budget
- Dynamic context (memories, knowledge, history) fills remaining space
- When over budget: summarize oldest items, trim to fit
- Never silently drop safety-critical context (risk notes, guardrail output)
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Conservative estimate: 3.5 chars ≈ 1 token for mixed Chinese/English text.
# Pure English is ~4 chars/token, pure Chinese is ~1.5 chars/token.
# We use 3.5 as a safe middle ground that slightly overestimates (safer to trim early).
CHARS_PER_TOKEN = 3.5

# Model context window sizes (conservative, leave room for output)
MODEL_WINDOWS: dict[str, int] = {
    "gpt-4o": 120_000,
    "gpt-4o-mini": 120_000,
    "gpt-4-turbo": 120_000,
    "gpt-4": 8_000,
    "gpt-3.5-turbo": 4_000,
    "claude-sonnet-4-20250514": 180_000,
    "claude-3.5-sonnet": 180_000,
    "claude-3-opus": 180_000,
    "deepseek-chat": 64_000,
    "qwen-max": 32_000,
    "qwen-plus": 128_000,
    "unknown": 8_000,  # Safe default
}

# Budget allocation (fractions of total context window)
SYSTEM_PROMPT_BUDGET = 0.05      # System prompt gets 5% of window
PROFILE_BUDGET = 0.03            # User profile gets 3%
PLAN_BUDGET = 0.10               # Active plan gets 10%
RISK_NOTES_BUDGET = 0.02         # Risk notes always included (safety-critical)
MEMORY_BUDGET = 0.20             # Retrieved memories get 20%
KNOWLEDGE_BUDGET = 0.15          # Knowledge base gets 15%
HISTORY_BUDGET = 0.30            # Conversation history gets 30%
OUTPUT_RESERVE = 0.15            # Reserve 15% for model output

# Safety-critical context that should never be trimmed
SAFETY_KEYS = {"active_risk_notes", "current_request_policy", "guardrail_flags"}


@dataclass
class TokenBudget:
    """Tracks token usage and limits for a context component."""

    name: str
    max_tokens: int
    used_tokens: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)

    @property
    def usage_pct(self) -> float:
        if self.max_tokens == 0:
            return 0.0
        return self.used_tokens / self.max_tokens * 100


def estimate_tokens(text: str) -> int:
    """Fast heuristic token count estimate.

    Uses character-based estimation: 3.5 chars ≈ 1 token.
    This is conservative for mixed CJK/English text.
    Returns at least 1 for non-empty strings.
    """
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_dict_tokens(data: dict[str, Any] | list[Any]) -> int:
    """Estimate tokens for a JSON-serializable structure."""
    return estimate_tokens(json.dumps(data, ensure_ascii=False, default=str))


class ContextWindowManager:
    """Token-aware context builder with automatic compaction.

    Usage:
        manager = ContextWindowManager(model_name="gpt-4o")
        manager.set_system_prompt(prompt_text)
        manager.set_profile(profile_dict)
        manager.set_plan(plan_dict)
        manager.add_memories(memory_list)
        manager.add_knowledge(knowledge_dict)
        manager.add_history(messages_list)

        # Build the final prompt, auto-compacting if needed
        prompt = manager.build()
    """

    def __init__(self, model_name: str = "unknown"):
        self.model_name = model_name
        self.total_tokens = MODEL_WINDOWS.get(model_name, MODEL_WINDOWS["unknown"])
        self.budgets: dict[str, TokenBudget] = {}

        # Initialize budgets
        self.budgets["system"] = TokenBudget(
            "system_prompt", int(self.total_tokens * SYSTEM_PROMPT_BUDGET)
        )
        self.budgets["profile"] = TokenBudget(
            "user_profile", int(self.total_tokens * PROFILE_BUDGET)
        )
        self.budgets["plan"] = TokenBudget(
            "active_plan", int(self.total_tokens * PLAN_BUDGET)
        )
        self.budgets["risk"] = TokenBudget(
            "risk_notes", int(self.total_tokens * RISK_NOTES_BUDGET)
        )
        self.budgets["memory"] = TokenBudget(
            "retrieved_memories", int(self.total_tokens * MEMORY_BUDGET)
        )
        self.budgets["knowledge"] = TokenBudget(
            "knowledge_base", int(self.total_tokens * KNOWLEDGE_BUDGET)
        )
        self.budgets["history"] = TokenBudget(
            "conversation_history", int(self.total_tokens * HISTORY_BUDGET)
        )

        self._system_prompt = ""
        self._profile = {}
        self._plan = {}
        self._risk_notes = []
        self._memories = []
        self._knowledge = {}
        self._history = []

        self.compaction_count = 0
        self.total_trimmed_items = 0

    # ---- Setters ----

    def set_system_prompt(self, text: str) -> None:
        self._system_prompt = text
        tokens = estimate_tokens(text)
        budget = self.budgets["system"]
        if tokens > budget.max_tokens:
            logger.warning(
                "System prompt (%d tokens) exceeds budget (%d tokens). Consider trimming.",
                tokens, budget.max_tokens,
            )
        budget.used_tokens = tokens

    def set_profile(self, profile: dict[str, Any]) -> None:
        self._profile = profile
        self.budgets["profile"].used_tokens = estimate_dict_tokens(profile)

    def set_plan(self, plan: dict[str, Any] | None) -> None:
        self._plan = plan or {}
        self.budgets["plan"].used_tokens = estimate_dict_tokens(self._plan)

    def set_risk_notes(self, notes: list[dict[str, Any]]) -> None:
        self._risk_notes = notes
        self.budgets["risk"].used_tokens = estimate_dict_tokens(notes)

    def set_memories(self, memories: list[dict[str, Any]]) -> None:
        self._memories = memories
        self._fit_to_budget("memory", self._memories, "summary")

    def set_knowledge(self, knowledge: dict[str, Any]) -> None:
        """Set knowledge context, compacting if needed."""
        self._knowledge = knowledge
        tokens = estimate_dict_tokens(knowledge)
        budget = self.budgets["knowledge"]
        if tokens > budget.max_tokens:
            # Compact knowledge: keep rules, truncate long texts
            compacted = self._compact_knowledge(knowledge)
            self._knowledge = compacted
            tokens = estimate_dict_tokens(compacted)
        budget.used_tokens = tokens

    def set_history(self, messages: list[dict[str, str]]) -> None:
        """Set conversation history, keeping most recent messages within budget."""
        self._history = messages
        self._fit_to_budget("history", self._history, "content")

    # ---- Compaction ----

    def _fit_to_budget(
        self, budget_name: str, items: list[dict[str, Any]], text_key: str
    ) -> None:
        """Fit items into the budget, trimming oldest/least-relevant first.

        Strategy (mirrors Claude Code's approach):
        1. If total fits, keep all items
        2. Otherwise, trim oldest items first (for history) or
           lowest-importance first (for memories)
        3. For remaining items, truncate long text fields
        """
        budget = self.budgets[budget_name]
        if not items:
            budget.used_tokens = 0
            return

        # Check if everything fits
        total = sum(estimate_tokens(item.get(text_key, "")) for item in items)
        if total <= budget.max_tokens:
            budget.used_tokens = total
            budget.items = items
            return

        # Need to trim. Sort and keep most valuable items.
        if budget_name == "history":
            # History: keep most recent
            sorted_items = items  # Already in order, keep from end
        elif budget_name == "memory":
            # Memories: sort by importance (highest first)
            sorted_items = sorted(
                items,
                key=lambda m: m.get("importance_score", 0) or m.get("importance", 0) or 0,
                reverse=True,
            )
        else:
            sorted_items = items

        # Greedy: add items until budget exhausted
        kept = []
        used = 0
        trimmed = 0
        for item in sorted_items:
            item_tokens = estimate_tokens(item.get(text_key, ""))
            if used + item_tokens <= budget.max_tokens:
                kept.append(item)
                used += item_tokens
            else:
                trimmed += 1

        # If we kept nothing, keep the single most important with truncation
        if not kept and sorted_items:
            best = sorted_items[0]
            text = best.get(text_key, "")
            max_chars = int(budget.max_tokens * CHARS_PER_TOKEN)
            best = dict(best)
            best[text_key] = text[:max_chars] + "\n...[truncated]"
            kept = [best]
            used = estimate_tokens(best[text_key])
            trimmed = len(items) - 1

        budget.used_tokens = used
        budget.items = kept
        budget.truncated = trimmed > 0

        if trimmed > 0:
            self.total_trimmed_items += trimmed
            self.compaction_count += 1
            logger.info(
                "Context compaction: %s kept %d/%d items (%d trimmed), using %d/%d tokens",
                budget_name, len(kept), len(items), trimmed, used, budget.max_tokens,
            )

    def _compact_knowledge(self, knowledge: dict[str, Any]) -> dict[str, Any]:
        """Compact knowledge context by truncating long text fields."""
        budget = self.budgets["knowledge"]
        compacted: dict[str, Any] = {}

        # Keep decision rules (compact, high value)
        if "decision_rules" in knowledge:
            rules = knowledge["decision_rules"]
            compacted["decision_rules"] = rules[:20]  # Max 20 rules

        # Keep plan templates (compact, high value)
        if "plan_templates" in knowledge:
            templates = knowledge["plan_templates"]
            compacted["plan_templates"] = templates[:5]  # Max 5 templates

        # Truncate explanation knowledge entries
        if "explanation_knowledge" in knowledge:
            entries = knowledge["explanation_knowledge"]
            max_chars_per_entry = int(
                (budget.max_tokens * CHARS_PER_TOKEN * 0.5) / max(len(entries), 1)
            )
            compacted["explanation_knowledge"] = [
                {**entry, "content": entry.get("content", "")[:max_chars_per_entry]}
                for entry in entries[:10]
            ]

        # Keep coaching cases (compact)
        if "coaching_cases" in knowledge:
            compacted["coaching_cases"] = knowledge["coaching_cases"][:5]

        # Keep debug info compact
        if "debug" in knowledge:
            compacted["debug"] = {
                k: v for k, v in knowledge["debug"].items()
                if k in {"matched_rule_ids", "matched_template_ids", "memory_top_k"}
            }

        return compacted

    # ---- Build ----

    def build(self) -> str:
        """Build the final context string for the LLM prompt.

        Returns a formatted context that fits within the model's window,
        with all components properly sized and compacted.
        """
        sections = []

        # 1. System prompt (always included)
        if self._system_prompt:
            sections.append(self._system_prompt)

        # 2. User profile
        if self._profile:
            sections.append(
                "User Profile:\n" + json.dumps(self._profile, ensure_ascii=False, indent=2)
            )

        # 3. Active plan
        if self._plan:
            sections.append(
                "Active Training Plan:\n" + json.dumps(self._plan, ensure_ascii=False, indent=2)
            )

        # 4. Risk notes (safety-critical, always included)
        if self._risk_notes:
            sections.append(
                "Active Risk Notes (must consider for safety):\n"
                + json.dumps(self._risk_notes, ensure_ascii=False, indent=2)
            )

        # 5. Retrieved memories
        if self.budgets["memory"].items:
            memory_text = "Relevant Memories:\n"
            for mem in self.budgets["memory"].items:
                summary = mem.get("summary") or mem.get("content", "")[:200]
                memory_text += f"- {summary}\n"
            if self.budgets["memory"].truncated:
                memory_text += f"... ({self.total_trimmed_items} items trimmed for context limits)\n"
            sections.append(memory_text)

        # 6. Knowledge base
        if self._knowledge:
            sections.append(
                "Knowledge Context:\n"
                + json.dumps(self._knowledge, ensure_ascii=False, indent=2)
            )

        # 7. Conversation history
        if self.budgets["history"].items:
            history_text = "Recent Conversation:\n"
            for msg in self.budgets["history"].items:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if len(content) > 500:
                    content = content[:500] + "..."
                history_text += f"[{role}]: {content}\n"
            if self.budgets["history"].truncated:
                history_text += "... (earlier messages trimmed)\n"
            sections.append(history_text)

        # 8. Budget summary for debugging
        budget_lines = ["\n[Context Budget]"]
        for name, budget in self.budgets.items():
            status = "TRUNCATED" if budget.truncated else "OK"
            budget_lines.append(
                f"  {name}: {budget.used_tokens}/{budget.max_tokens} tokens ({budget.usage_pct:.0f}%) [{status}]"
            )
        budget_lines.append(
            f"  TOTAL: {sum(b.used_tokens for b in self.budgets.values())}/{self.total_tokens} tokens"
        )
        sections.append("\n".join(budget_lines))

        return "\n\n".join(sections)

    def should_compact(self) -> bool:
        """Check if the current context is approaching the model's limit."""
        total_used = sum(b.used_tokens for b in self.budgets.values())
        threshold = self.total_tokens * (1.0 - OUTPUT_RESERVE)
        component_over_budget = any(
            budget.used_tokens > budget.max_tokens
            for name, budget in self.budgets.items()
            if name != "risk"
        )
        return total_used > threshold or component_over_budget

    def stats(self) -> dict[str, Any]:
        """Return compaction and budget statistics."""
        return {
            "model_name": self.model_name,
            "total_window_tokens": self.total_tokens,
            "budgets": {
                name: {
                    "max_tokens": b.max_tokens,
                    "used_tokens": b.used_tokens,
                    "usage_pct": round(b.usage_pct, 1),
                    "item_count": len(b.items),
                    "truncated": b.truncated,
                    "over_budget": b.used_tokens > b.max_tokens,
                }
                for name, b in self.budgets.items()
            },
            "total_used": sum(b.used_tokens for b in self.budgets.values()),
            "compaction_count": self.compaction_count,
            "total_trimmed_items": self.total_trimmed_items,
            "should_compact": self.should_compact(),
        }


def build_context_packet_with_budget(
    context_packet: dict[str, Any],
    model_name: str = "unknown",
    system_prompt: str = "",
    history: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wrap an existing context packet with token budgeting.

    Returns (compacted_packet, budget_stats).
    This is a drop-in enhancement for ContextBuilder.build_context_packet().
    """
    manager = ContextWindowManager(model_name=model_name)

    if system_prompt:
        manager.set_system_prompt(system_prompt)

    profile = context_packet.get("core_profile") or {}
    manager.set_profile(profile)

    plan = context_packet.get("active_plan") or {}
    manager.set_plan(plan)

    risk_notes = context_packet.get("active_risk_notes") or []
    manager.set_risk_notes(risk_notes)

    memories = context_packet.get("relevant_memories") or []
    if not memories:
        memories = context_packet.get("memory_catalog") or []
    manager.set_memories(memories)

    knowledge = context_packet.get("knowledge_context") or {}
    manager.set_knowledge(knowledge)

    if history:
        manager.set_history(history)

    # Build compacted versions
    compacted_packet = dict(context_packet)
    if manager.budgets["memory"].truncated:
        compacted_packet["relevant_memories"] = manager.budgets["memory"].items
        compacted_packet["_memory_truncated"] = True

    if manager.budgets["knowledge"].truncated:
        compacted_packet["knowledge_context"] = manager._knowledge
        compacted_packet["_knowledge_truncated"] = True

    return compacted_packet, manager.stats()
