import copy
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.bm25 import build_weighted_document, rank_by_bm25
from fast_api.app.services.model_provider import ModelProvider

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "data" / "fitness_knowledge"


class FitnessKnowledgeService:
    """Fitness knowledge split into RAG, decision rules, templates, and cases."""

    def __init__(self, db: Session | None, model_provider: ModelProvider | None = None):
        self.db = db
        self.model_provider = model_provider or ModelProvider()

    def seed_builtin_knowledge(self) -> dict[str, int]:
        if self.db is None:
            return {"explanation": 0, "decision_rules": 0, "plan_templates": 0, "coaching_cases": 0}

        counts = {
            "explanation": self._seed_explanations(),
            "decision_rules": self._seed_decision_rules(),
            "plan_templates": self._seed_plan_templates(),
            "coaching_cases": self._seed_coaching_cases(),
        }
        self.db.commit()
        return counts

    def build_knowledge_context(
        self,
        intent: str,
        query: str,
        context_packet: dict[str, Any],
    ) -> dict[str, Any]:
        rule_context = self._augment_query_signals(context_packet, query)
        decision_rules = self.match_decision_rules(intent, rule_context, query=query)
        plan_templates = self.select_plan_templates(intent, context_packet, query=query)
        explanation_knowledge = self.retrieve_explanation_knowledge(intent, query, context_packet)
        coaching_cases = self.retrieve_coaching_cases(intent, query, context_packet)
        embedding_mode = self.model_provider.embedding_mode()
        vector_available = embedding_mode != "vector_unavailable_bm25_fallback"
        return {
            "embedding_mode": embedding_mode,
            "explanation_knowledge": explanation_knowledge,
            "decision_rules": decision_rules,
            "plan_templates": plan_templates,
            "coaching_cases": coaching_cases,
            "debug": {
                "intent": intent,
                "query": query,
                "retrieval_ranker": "hybrid_vector_bm25" if vector_available else "bm25",
                "vector_available": vector_available,
                "vector_status": "available" if vector_available else "vector unavailable",
                "bm25_enabled": True,
                "rag_used_for": ["explanation_knowledge", "coaching_cases"],
                "structured_used_for": ["decision_rules", "plan_templates"],
                "matched_knowledge_ids": [item["knowledge_id"] for item in explanation_knowledge],
                "matched_knowledge_scores": [
                    {
                        "knowledge_id": item["knowledge_id"],
                        "bm25_score": item.get("bm25_score", 0.0),
                    }
                    for item in explanation_knowledge
                ],
                "matched_rule_ids": [item["rule_id"] for item in decision_rules],
                "matched_template_ids": [item["template_id"] for item in plan_templates],
                "matched_case_ids": [item["case_id"] for item in coaching_cases],
                "matched_case_scores": [
                    {"case_id": item["case_id"], "bm25_score": item.get("bm25_score", 0.0)}
                    for item in coaching_cases
                ],
            },
        }

    def retrieve_explanation_knowledge(
        self,
        intent: str,
        query: str,
        context_packet: dict[str, Any] | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        if intent == "memory_query":
            return []
        ranking_query = self._expand_retrieval_query(query)
        if self.db is None:
            items = self._load_json("explanation_knowledge.json")
            return [
                self._explanation_from_seed(match.item, match.normalized_score)
                for match in self._rank_seed_explanations(items, ranking_query)[:top_k]
            ]

        filters = [models.ExplanationKnowledge.status == "active"]
        candidates = self._vector_query(models.ExplanationKnowledge, filters, query, top_k * 3)
        if not candidates:
            candidates = list(
                self.db.scalars(
                    select(models.ExplanationKnowledge)
                    .where(*filters)
                    .order_by(desc(models.ExplanationKnowledge.updated_at))
                    .limit(top_k * 3)
                )
            )
        ranked = self._rank_explanation_models(candidates, ranking_query)[:top_k]
        return [self._explanation_payload(match.item, match.normalized_score) for match in ranked]

    def retrieve_coaching_cases(
        self,
        intent: str,
        query: str,
        context_packet: dict[str, Any] | None = None,
        top_k: int = 2,
    ) -> list[dict[str, Any]]:
        if intent in {"training_log"}:
            return []
        ranking_query = self._expand_retrieval_query(query)
        if self.db is None:
            items = self._load_json("coaching_cases.json")
            return [
                self._case_from_seed(match.item, match.normalized_score)
                for match in self._rank_seed_cases(items, ranking_query)[:top_k]
            ]

        filters = [models.CoachingCase.status == "active"]
        candidates = self._vector_query(models.CoachingCase, filters, query, top_k * 3)
        if not candidates:
            candidates = list(
                self.db.scalars(
                    select(models.CoachingCase)
                    .where(*filters)
                    .order_by(desc(models.CoachingCase.updated_at))
                    .limit(top_k * 3)
                )
            )
        ranked = self._rank_case_models(candidates, ranking_query)[:top_k]
        return [self._case_payload(match.item, match.normalized_score) for match in ranked]

    def match_decision_rules(
        self,
        intent: str,
        context_packet: dict[str, Any],
        limit: int = 5,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        rule_context = self._augment_query_signals(context_packet, query or "")
        rules = self._load_rules(intent)
        matched = []
        for rule in rules:
            condition = rule.get("condition_json") or {}
            if self._condition_matches(condition, rule_context):
                matched.append(rule)
        matched.sort(key=lambda item: item.get("priority", 0), reverse=True)
        return matched[:limit]

    def select_plan_templates(
        self,
        intent: str,
        context_packet: dict[str, Any],
        limit: int = 3,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        query = query or ""
        lowered_query = query.lower()
        inferred_training_query = any(
            term in lowered_query
            for term in [
                "训练计划",
                "健身计划",
                "训练分化",
                "哑铃",
                "零基础",
                "器材",
                "开始锻炼",
                "力量训练",
            ]
        )
        effective_intent = intent
        if intent in {"general_chat", "training_log"} and inferred_training_query:
            effective_intent = "training_plan"
        if effective_intent not in {"training_plan", "nutrition_advice", "progression_decision"}:
            return []
        profile = context_packet.get("core_profile") or {}
        goal = profile.get("goal") or self._infer_goal_from_query(lowered_query)
        level = profile.get("experience_level") or self._infer_level_from_query(lowered_query)
        frequency = profile.get("workout_frequency") or self._infer_frequency_from_query(
            lowered_query
        )
        equipment = [str(item).lower() for item in profile.get("equipment_available") or []]
        if not equipment:
            equipment = self._infer_equipment_from_query(lowered_query)
        explicit_equipment = bool(equipment)
        wanted_type = "nutrition" if effective_intent == "nutrition_advice" else "training"
        templates = self._load_templates()
        scored = []
        for template in templates:
            if template.get("template_type") != wanted_type:
                continue
            score = 0
            if goal and template.get("goal") == goal:
                score += 4
            if level and template.get("level") in {level, None}:
                score += 2
            if frequency and template.get("days_per_week") in {frequency, None}:
                score += 2
            template_equipment = [str(item).lower() for item in template.get("equipment") or []]
            if explicit_equipment:
                if "bodyweight" in equipment and "bodyweight" not in template_equipment:
                    continue
                if "home" in equipment and any(
                    item in {"gym", "machines", "barbell", "cable", "power_rack", "specialty_bars"}
                    for item in template_equipment
                ):
                    continue
                if template_equipment and any(item in equipment for item in template_equipment):
                    score += 6
            if wanted_type == "nutrition":
                memory_text = json.dumps(
                    context_packet.get("relevant_memories") or [], ensure_ascii=False
                )
                if "不自己做饭" in memory_text or "外食" in memory_text or "takeout" in memory_text:
                    score += 4
            if score > 0 or wanted_type == "nutrition":
                scored.append((score, template))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._template_payload(item[1], item[0]) for item in scored[:limit]]

    def _augment_query_signals(
        self,
        context_packet: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        """Build transient rule features from the current message.

        These values are not persisted as user facts. They bridge the common
        cold-start gap where a single message contains a short-lived state
        (for example, ``月经前`` or ``训练了 5 周``) that is not yet present in
        the structured profile or logs.
        """
        packet = copy.deepcopy(context_packet)
        lowered = query.lower()
        profile = packet.setdefault("core_profile", {})
        recovery = packet.setdefault("recent_recovery", [])
        checkins = packet.setdefault("recent_checkins", [])
        history = packet.setdefault("exercise_history", [])
        memories = packet.setdefault("relevant_memories", [])

        def ensure_recovery(*, overwrite: bool = False, **values: Any) -> None:
            row = recovery[0] if recovery and isinstance(recovery[0], dict) else {}
            row.update({key: value for key, value in values.items() if overwrite or key not in row})
            if recovery:
                recovery[0] = row
            else:
                recovery.append(row)

        def ensure_checkin(*, overwrite: bool = False, **values: Any) -> None:
            row = checkins[0] if checkins and isinstance(checkins[0], dict) else {}
            row.update({key: value for key, value in values.items() if overwrite or key not in row})
            if checkins:
                checkins[0] = row
            else:
                checkins.append(row)

        if any(term in lowered for term in ["月经前", "黄体"]):
            profile["biological_sex"] = "female"
            ensure_checkin(overwrite=True, menstrual_phase="luteal")
            ensure_recovery(overwrite=True, fatigue_score=7)
        elif any(term in lowered for term in ["月经刚结束", "卵泡"]):
            profile["biological_sex"] = "female"
            ensure_checkin(overwrite=True, menstrual_phase="follicular")
            ensure_recovery(overwrite=True, fatigue_score=4, sleep_hours=8)
        elif any(term in lowered for term in ["月经第一天", "经期", "痛经"]):
            profile["biological_sex"] = "female"
            ensure_checkin(overwrite=True, menstrual_phase="menstrual", cramps_severity=8)

        if any(term in lowered for term in ["没有空调", "夏天", "很热", "出很多汗"]):
            profile.setdefault("training_environment", "hot")
            ensure_checkin(sweat_level="heavy", hydration_concern=True)

        week_match = re.search(r"(?:连续)?训练(?:了)?\s*(\d+)\s*周", lowered)
        if week_match:
            profile["consecutive_training_weeks"] = int(week_match.group(1))

        rpe_match = re.search(r"rpe\s*(\d+)", lowered)
        if rpe_match:
            history.append({"rpe": int(rpe_match.group(1))})

        if "睡眠和疲劳都还好" in lowered or "睡眠和疲劳都还不错" in lowered:
            ensure_recovery(overwrite=True, sleep_hours=7, fatigue_score=5, motivation_score=7)

        if any(
            term in lowered
            for term in [
                "不想练",
                "不想训练",
                "不想去训练",
                "动力下降",
                "动力也没了",
                "提不起劲",
            ]
        ):
            ensure_recovery(overwrite=True, motivation_score=2)
        if any(term in lowered for term in ["静息心率升高", "静息心率比平时高", "心率比平时高"]):
            ensure_checkin(overwrite=True, resting_hr_elevated=True)

        if "训练前和训练后" in lowered or "训练前后" in lowered:
            rpe_match = re.search(r"rpe\s*(\d+)", lowered)
            duration_match = re.search(r"(\d+)\s*分钟", lowered)
            if rpe_match or duration_match:
                history.append(
                    {
                        "rpe": int(rpe_match.group(1)) if rpe_match else 8,
                        "duration_min": int(duration_match.group(1)) if duration_match else 60,
                    }
                )

        if any(term in lowered for term in ["医生允许", "刚被医生允许", "受伤刚"]):
            memories.append({"memory_type": "injury_history", "content": "recent injury"})
            ensure_recovery(pain_score=6)

        if "肩伤" in lowered or "肩膀" in lowered:
            memories.append({"content": "肩伤"})
            history.append({"exercise_name": "overhead_press", "pain_score": 5})

        if "动作不太对" in lowered or "动作不对" in lowered:
            profile.setdefault("training_experience_months", 2)
            history.append({"form_quality_score": 5})

        return packet

    def _infer_goal_from_query(self, query: str) -> str | None:
        if any(term in query for term in ["增肌", "肌肉"]):
            return "muscle_gain"
        if any(term in query for term in ["减脂", "减肥"]):
            return "fat_loss"
        return None

    def _infer_level_from_query(self, query: str) -> str | None:
        if any(term in query for term in ["中阶", "一年多", "中级"]):
            return "intermediate"
        if any(term in query for term in ["新手", "零基础", "完全没去过", "刚开始"]):
            return "beginner"
        return None

    def _infer_frequency_from_query(self, query: str) -> int | None:
        match = re.search(r"每周能?练\s*(\d+)\s*天", query)
        return int(match.group(1)) if match else None

    def _infer_equipment_from_query(self, query: str) -> list[str]:
        if "哑铃" in query:
            return ["dumbbell", "home"]
        if any(term in query for term in ["没有任何器材", "无器材", "徒手"]):
            return ["bodyweight"]
        if "健身房" in query:
            return ["gym"]
        return []

    def _expand_retrieval_query(self, query: str) -> str:
        """Add small, reviewed domain synonyms before lexical retrieval."""

        lowered = query.lower()
        expansions: list[str] = []
        groups = (
            (
                ["平台", "停滞", "不涨", "没有进步", "stall", "plateau"],
                "周期化 periodization 渐进超负荷 训练变量",
            ),
            (
                ["睡眠不足", "睡不好", "只睡", "失眠", "poor sleep"],
                "睡眠 恢复 深度睡眠 生长激素 sleep recovery",
            ),
            (
                ["新手", "零基础", "动作不对", "动作不太对", "动作质量"],
                "新手适应 神经适应 动作模式 beginner adaptation",
            ),
            (["减载", "连续训练", "休息周", "deload"], "减载周 deload 恢复 连续训练"),
            (["月经", "黄体", "卵泡", "痛经"], "月经周期 黄体期 卵泡期 女性训练 menstrual cycle"),
            (
                [
                    "高温",
                    "很热",
                    "特别热",
                    "夏天",
                    "出汗",
                    "很多汗",
                    "喝水",
                    "多少水",
                    "补水",
                    "hydration",
                ],
                "补水 脱水 电解质 高温训练 hydration performance",
            ),
        )
        for triggers, addition in groups:
            if any(trigger in lowered for trigger in triggers):
                expansions.append(addition)
        return " ".join([query, *expansions])

    def _seed_explanations(self) -> int:
        count = 0
        for item in self._load_json("explanation_knowledge.json"):
            existing = self.db.scalar(
                select(models.ExplanationKnowledge).where(
                    models.ExplanationKnowledge.knowledge_id == item["knowledge_id"]
                )
            )
            if existing is None:
                existing = models.ExplanationKnowledge(knowledge_id=item["knowledge_id"])
                self.db.add(existing)
            existing.topic = item["topic"]
            existing.content = item["content"]
            existing.tags = item.get("tags") or []
            existing.source = item.get("source", "seed")
            existing.safety_level = item.get("safety_level", "general")
            existing.status = item.get("status", "active")
            existing.embedding = self.model_provider.embed_text(
                f"{existing.topic}\n{existing.content}\n{' '.join(existing.tags)}"
            )
            count += 1
        self.db.flush()
        return count

    def _seed_decision_rules(self) -> int:
        count = 0
        for item in self._load_json("decision_rules.json"):
            existing = self.db.scalar(
                select(models.FitnessDecisionRule).where(
                    models.FitnessDecisionRule.rule_id == item["rule_id"]
                )
            )
            if existing is None:
                existing = models.FitnessDecisionRule(rule_id=item["rule_id"])
                self.db.add(existing)
            existing.rule_type = item["rule_type"]
            existing.intent = item["intent"]
            existing.description = item["description"]
            existing.condition_json = item.get("condition_json") or {}
            existing.action_json = item.get("action_json") or {}
            existing.priority = int(item.get("priority", 50))
            existing.safety_level = item.get("safety_level", "general")
            existing.enabled = bool(item.get("enabled", True))
            existing.version = item.get("version", "v1")
            count += 1
        self.db.flush()
        return count

    def _seed_plan_templates(self) -> int:
        count = 0
        for item in self._load_json("plan_templates.json"):
            existing = self.db.scalar(
                select(models.PlanTemplate).where(
                    models.PlanTemplate.template_id == item["template_id"]
                )
            )
            if existing is None:
                existing = models.PlanTemplate(template_id=item["template_id"])
                self.db.add(existing)
            existing.template_type = item["template_type"]
            existing.goal = item.get("goal")
            existing.level = item.get("level")
            existing.days_per_week = item.get("days_per_week")
            existing.equipment = item.get("equipment") or []
            existing.template_json = item.get("template_json") or {}
            existing.constraints = item.get("constraints") or {}
            existing.version = item.get("version", "v1")
            existing.enabled = bool(item.get("enabled", True))
            count += 1
        self.db.flush()
        return count

    def _seed_coaching_cases(self) -> int:
        count = 0
        for item in self._load_json("coaching_cases.json"):
            existing = self.db.scalar(
                select(models.CoachingCase).where(models.CoachingCase.case_id == item["case_id"])
            )
            if existing is None:
                existing = models.CoachingCase(case_id=item["case_id"])
                self.db.add(existing)
            existing.case_type = item["case_type"]
            existing.title = item.get("title") or item["case_id"]
            existing.profile_summary = item.get("profile_summary")
            existing.scenario = item.get("scenario") or item["situation"]
            existing.situation = item["situation"]
            existing.approach = item.get("approach") or item["coach_response_pattern"]
            existing.coach_response_pattern = item["coach_response_pattern"]
            existing.key_principles = item.get("key_principles") or []
            existing.tags = item.get("tags") or []
            existing.source = item.get("source", "seed")
            existing.status = item.get("status", "active")
            existing.embedding = self.model_provider.embed_text(
                f"{existing.case_type}\n{existing.situation}\n{existing.coach_response_pattern}"
            )
            count += 1
        self.db.flush()
        return count

    def _load_rules(self, intent: str) -> list[dict[str, Any]]:
        if self.db is None:
            rules = self._load_json("decision_rules.json")
        else:
            db_rules = list(
                self.db.scalars(
                    select(models.FitnessDecisionRule)
                    .where(models.FitnessDecisionRule.enabled.is_(True))
                    .order_by(desc(models.FitnessDecisionRule.priority))
                )
            )
            rules = [self._rule_payload(rule) for rule in db_rules]
        aliases = {intent}
        aliases.update(
            {
                "injury_or_risk": {"training_plan", "exercise_selection"},
                "recovery_check": {"progression_decision"},
                "training_log": {
                    "training_plan",
                    "exercise_selection",
                    "progression_decision",
                    "nutrition_advice",
                },
                "nutrition_log": {"nutrition_advice"},
                "general_chat": {
                    "training_plan",
                    "exercise_selection",
                    "progression_decision",
                    "nutrition_advice",
                },
            }.get(intent, set())
        )
        return [rule for rule in rules if rule.get("intent") in aliases]

    def _load_templates(self) -> list[dict[str, Any]]:
        if self.db is None:
            return self._load_json("plan_templates.json")
        templates = list(
            self.db.scalars(
                select(models.PlanTemplate)
                .where(models.PlanTemplate.enabled.is_(True))
                .order_by(models.PlanTemplate.template_type, models.PlanTemplate.template_id)
            )
        )
        return [self._template_model_payload(template) for template in templates]

    def _condition_matches(self, condition: dict[str, Any], context: dict[str, Any]) -> bool:
        if not condition:
            return True
        has_group = "all" in condition or "any" in condition
        all_matches = all(
            self._predicate_matches(predicate, context) for predicate in condition.get("all", [])
        )
        any_matches = (
            any(self._predicate_matches(predicate, context) for predicate in condition["any"])
            if condition.get("any")
            else True
        )
        if has_group:
            return all_matches and any_matches
        return self._predicate_matches(condition, context)

    def _predicate_matches(self, predicate: dict[str, Any], context: dict[str, Any]) -> bool:
        values = self._extract_path_values(context, str(predicate.get("path", "")))
        op = predicate.get("op")
        target = predicate.get("value")
        if not values:
            return False
        if op == "contains":
            return any(
                str(target).lower() in str(value).lower() for value in values if value is not None
            )
        if op == ">=":
            return any(
                self._to_float(value) is not None and self._to_float(value) >= float(target)
                for value in values
            )
        if op == "<=":
            return any(
                self._to_float(value) is not None and self._to_float(value) <= float(target)
                for value in values
            )
        if op == "<":
            return any(
                self._to_float(value) is not None and self._to_float(value) < float(target)
                for value in values
            )
        if op == "==":
            return any(value == target for value in values)
        return False

    def _extract_path_values(self, data: Any, path: str) -> list[Any]:
        if not path:
            return []
        parts = path.split(".")
        values = [data]
        for part in parts:
            next_values: list[Any] = []
            is_wildcard = part.endswith("[*]")
            is_index_zero = part.endswith("[0]")
            key = part[:-3] if is_wildcard or is_index_zero else part
            for value in values:
                current = value.get(key) if isinstance(value, dict) else None
                if is_wildcard and isinstance(current, list):
                    next_values.extend(current)
                elif is_index_zero and isinstance(current, list) and current:
                    next_values.append(current[0])
                elif current is not None:
                    next_values.append(current)
            values = next_values
        flattened: list[Any] = []
        for value in values:
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened

    def _vector_query(
        self, model_class: Any, filters: list[Any], query: str, limit: int
    ) -> list[Any]:
        if self.db is None:
            return []
        try:
            query_embedding = self.model_provider.embed_text(query)
            if query_embedding is None:
                return []
            return list(
                self.db.scalars(
                    select(model_class)
                    .where(*filters, model_class.embedding.is_not(None))
                    .order_by(model_class.embedding.cosine_distance(query_embedding))
                    .limit(limit)
                )
            )
        except Exception:
            return []

    def _rank_explanation_models(self, items: list[Any], query: str):
        return sorted(
            rank_by_bm25(items, query, self._explanation_model_document),
            key=lambda match: match.normalized_score,
            reverse=True,
        )

    def _rank_case_models(self, items: list[Any], query: str):
        return sorted(
            rank_by_bm25(items, query, self._case_model_document),
            key=lambda match: match.normalized_score,
            reverse=True,
        )

    def _rank_seed_explanations(self, items: list[dict[str, Any]], query: str):
        return sorted(
            rank_by_bm25(items, query, self._explanation_seed_document),
            key=lambda match: match.normalized_score,
            reverse=True,
        )

    def _rank_seed_cases(self, items: list[dict[str, Any]], query: str):
        return sorted(
            rank_by_bm25(items, query, self._case_seed_document),
            key=lambda match: match.normalized_score,
            reverse=True,
        )

    def _explanation_model_document(self, item: models.ExplanationKnowledge) -> str:
        return build_weighted_document(
            [
                (item.topic, 4),
                (" ".join(item.tags or []), 3),
                (item.content, 5),
                (item.safety_level, 1),
            ]
        )

    def _case_model_document(self, item: models.CoachingCase) -> str:
        return build_weighted_document(
            [
                (item.case_type, 2),
                (item.profile_summary, 2),
                (item.situation, 5),
                (item.coach_response_pattern, 3),
                (" ".join(item.tags or []), 4),
            ]
        )

    def _explanation_seed_document(self, item: dict[str, Any]) -> str:
        return build_weighted_document(
            [
                (item.get("topic"), 4),
                (" ".join(item.get("tags") or []), 3),
                (item.get("content"), 5),
                (item.get("safety_level"), 1),
            ]
        )

    def _case_seed_document(self, item: dict[str, Any]) -> str:
        return build_weighted_document(
            [
                (item.get("case_type"), 2),
                (item.get("profile_summary"), 2),
                (item.get("situation"), 5),
                (item.get("coach_response_pattern"), 3),
                (" ".join(item.get("tags") or []), 4),
            ]
        )

    def _load_json(self, filename: str) -> list[dict[str, Any]]:
        with (KNOWLEDGE_DIR / filename).open("r", encoding="utf-8") as file:
            return json.load(file)

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _explanation_payload(
        self, item: models.ExplanationKnowledge, bm25_score: float = 0.0
    ) -> dict[str, Any]:
        return {
            "knowledge_id": item.knowledge_id,
            "topic": item.topic,
            "content": item.content,
            "tags": item.tags or [],
            "source": item.source,
            "safety_level": item.safety_level,
            "bm25_score": round(bm25_score, 4),
        }

    def _explanation_from_seed(
        self, item: dict[str, Any], bm25_score: float = 0.0
    ) -> dict[str, Any]:
        return {
            "knowledge_id": item["knowledge_id"],
            "topic": item["topic"],
            "content": item["content"],
            "tags": item.get("tags") or [],
            "source": item.get("source", "seed"),
            "safety_level": item.get("safety_level", "general"),
            "bm25_score": round(bm25_score, 4),
        }

    def _rule_payload(self, item: models.FitnessDecisionRule) -> dict[str, Any]:
        return {
            "rule_id": item.rule_id,
            "rule_type": item.rule_type,
            "intent": item.intent,
            "description": item.description,
            "condition_json": item.condition_json or {},
            "action_json": item.action_json or {},
            "priority": item.priority,
            "safety_level": item.safety_level,
            "version": item.version,
        }

    def _template_model_payload(self, item: models.PlanTemplate) -> dict[str, Any]:
        return {
            "template_id": item.template_id,
            "template_type": item.template_type,
            "goal": item.goal,
            "level": item.level,
            "days_per_week": item.days_per_week,
            "equipment": item.equipment or [],
            "template_json": item.template_json or {},
            "constraints": item.constraints or {},
            "version": item.version,
        }

    def _template_payload(self, item: dict[str, Any], score: int) -> dict[str, Any]:
        return {**item, "match_score": score}

    def _case_payload(self, item: models.CoachingCase, bm25_score: float = 0.0) -> dict[str, Any]:
        return {
            "case_id": item.case_id,
            "case_type": item.case_type,
            "profile_summary": item.profile_summary,
            "situation": item.situation,
            "coach_response_pattern": item.coach_response_pattern,
            "tags": item.tags or [],
            "source": item.source,
            "bm25_score": round(bm25_score, 4),
        }

    def _case_from_seed(self, item: dict[str, Any], bm25_score: float = 0.0) -> dict[str, Any]:
        return {
            "case_id": item["case_id"],
            "case_type": item["case_type"],
            "profile_summary": item.get("profile_summary"),
            "situation": item["situation"],
            "coach_response_pattern": item["coach_response_pattern"],
            "tags": item.get("tags") or [],
            "source": item.get("source", "seed"),
            "bm25_score": round(bm25_score, 4),
        }
