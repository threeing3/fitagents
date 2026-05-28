"""
Evaluation service — orchestrates LLM output quality assessment.

Architecture:
1. Rule-based checks (fast, deterministic)
2. LLM-as-judge scoring (semantic quality)
3. Aggregate scoring with weighted dimensions
4. Batch suite execution for regression testing

Reference: RAGAS, DeepEval, LangSmith eval framework patterns.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from fast_api.app.core.prompts import registry
from fast_api.app.core.eval_metrics import (
    EvalDimension,
    EvalResult as MetricsResult,
    check_safety_rules,
    compute_aggregate_scores,
    make_judge_prompt,
    JUDGE_SYSTEM_PROMPT,
)
from fast_api.app.db import models
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)


class EvalService:
    """Orchestrates LLM output evaluation across multiple dimensions.

    Usage:
        service = EvalService(db, model_provider)
        # Single response evaluation
        result = await service.evaluate("user msg", "coach response")
        # Batch run
        run = await service.run_suite("regression-v1", cases)
    """

    def __init__(
        self,
        db: Session,
        model_provider: ModelProvider | None = None,
        coach_service: Any | None = None,  # CoachAgentService
    ):
        self.db = db
        self.model_provider = model_provider or ModelProvider()
        self.coach_service = coach_service

    # ----------------------------------------------------------------
    # Single response evaluation
    # ----------------------------------------------------------------

    async def evaluate(
        self,
        user_message: str,
        coach_response: str,
        context: str | None = None,
        ground_truth: str | None = None,
        dimensions: list[EvalDimension] | None = None,
        eval_case: models.EvalCase | None = None,
    ) -> MetricsResult:
        """Evaluate a single coach response across all dimensions.

        Args:
            user_message: The user's original query.
            coach_response: The coach's generated response.
            context: Retrieved knowledge context (for faithfulness check).
            ground_truth: Expected/reference answer (optional).
            dimensions: Subset of dimensions to evaluate.
            eval_case: Optional EvalCase to compute expected_scores against.

        Returns:
            MetricsResult with dimension scores and pass/fail.
        """
        # 1. Rule-based safety checks
        rule_results = check_safety_rules(coach_response)

        # 2. LLM-as-judge scoring
        judges_scores = await self._run_llm_judge(
            user_message, coach_response, context, ground_truth,
        )

        # 3. Aggregate
        result = compute_aggregate_scores(
            judges_scores, rule_results, dimensions=dimensions,
        )

        # 4. Check against expected scores (if eval_case provided)
        if eval_case and eval_case.expected_scores:
            self._check_expected_scores(result, eval_case.expected_scores)

        return result

    # ----------------------------------------------------------------
    # LLM-as-judge
    # ----------------------------------------------------------------

    async def _run_llm_judge(
        self,
        user_message: str,
        coach_response: str,
        context: str | None = None,
        ground_truth: str | None = None,
    ) -> dict[str, Any] | None:
        """Score the response using a separate judge LLM.

        Uses a smaller/cheaper model (gpt-4o-mini) as the judge when
        available. Falls back to None (rule-based only) when no model.
        """
        # Get judge model — uses the vision model's config (cheaper)
        judge_model = self.model_provider.vision_model()
        if judge_model is None:
            logger.info("No judge model available — skipping LLM-as-judge scoring")
            return None

        prompt = make_judge_prompt(user_message, coach_response, context, ground_truth)

        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            response = await judge_model.ainvoke([
                SystemMessage(content=JUDGE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            text = str(response.content)
            return self._parse_judge_json(text)
        except Exception as exc:
            logger.warning("LLM judge invocation failed: %s", exc)
            return None

    def _parse_judge_json(self, text: str) -> dict[str, Any] | None:
        """Parse the judge LLM's JSON response, tolerating markdown fences."""
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse judge response: %s", text[:150])
            return None

    @staticmethod
    def _check_expected_scores(
        result: MetricsResult,
        expected_scores: dict[str, float],
    ) -> None:
        """Mark result as failed if any dimension is below its expected score."""
        for ds in result.dimension_scores:
            dim_key = ds.dimension.value
            if dim_key in expected_scores:
                if ds.score < expected_scores[dim_key]:
                    ds.passed = False

    # ----------------------------------------------------------------
    # Batch suite execution
    # ----------------------------------------------------------------

    async def run_suite(
        self,
        suite_name: str,
        cases: list[dict[str, Any]] | None = None,
        persist: bool = True,
        model_used: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        """Run an evaluation suite against a list of test cases.

        If cases is None, loads from the eval_cases.json file.

        Args:
            suite_name: Name for this eval run (e.g. "regression-v1").
            cases: List of case dicts, each with:
                - "input": user message
                - "expected": dict with ground_truth, must_include, expected_scores, etc.
            persist: Whether to save results to the database.
            model_used: Which model generated the responses.
            prompt_version: Which prompt version was used.

        Returns:
            Run summary with scores and pass rates.
        """
        if cases is None:
            cases = self._load_json_cases()

        run_id = uuid.uuid4()
        started_at = datetime.now(timezone.utc)

        results_list: list[dict[str, Any]] = []
        dimensions_accum: dict[str, list[float]] = {}

        for case in cases:
            case_name = case.get("name", "unnamed")
            inp = case.get("input", "")
            expected = case.get("expected", {})
            ground_truth = expected.get("ground_truth")
            context_text = expected.get("context")
            dimensions = self._parse_dimensions(expected.get("eval_dimensions"))

            # Generate response if coach_service is available
            if self.coach_service is not None and inp:
                response = await self._generate_response(inp)
            else:
                response = expected.get("reference_response") or ""

            if not response:
                logger.warning("Skipping case %s: no response available", case_name)
                continue

            # Evaluate
            eval_result = await self.evaluate(
                user_message=inp,
                coach_response=response,
                context=context_text,
                ground_truth=ground_truth,
                dimensions=dimensions,
            )

            # Accumulate dimension scores
            for ds in eval_result.dimension_scores:
                dim_key = ds.dimension.value
                if dim_key not in dimensions_accum:
                    dimensions_accum[dim_key] = []
                dimensions_accum[dim_key].append(ds.score)

            result_entry = {
                "case_name": case_name,
                "score": eval_result.overall_score,
                "passed": eval_result.passed,
                "dimensions": {
                    ds.dimension.value: {"score": ds.score, "passed": ds.passed, "explanation": ds.explanation}
                    for ds in eval_result.dimension_scores
                },
                "summary": eval_result.summary,
                "rule_checks": eval_result.rule_checks,
            }
            results_list.append(result_entry)

        # Compute aggregate
        total = len(results_list)
        passed_count = sum(1 for r in results_list if r["passed"])
        avg_score = (
            round(sum(r["score"] for r in results_list) / total, 2) if total > 0 else 0.0
        )
        dimension_averages = {
            dim: round(sum(scores) / len(scores), 2)
            for dim, scores in dimensions_accum.items()
        }

        completed_at = datetime.now(timezone.utc)

        # Persist
        if persist:
            self._persist_run(
                run_id=run_id,
                suite_name=suite_name,
                model_used=model_used or "unknown",
                prompt_version=prompt_version,
                total_cases=total,
                passed_count=passed_count,
                average_score=avg_score,
                dimension_averages=dimension_averages,
                started_at=started_at,
                completed_at=completed_at,
                results=results_list,
            )

        return {
            "run_id": str(run_id),
            "suite_name": suite_name,
            "total_cases": total,
            "passed_count": passed_count,
            "average_score": avg_score,
            "dimension_averages": dimension_averages,
            "pass_rate": round(passed_count / total, 2) if total > 0 else 0.0,
            "results": results_list,
        }

    async def _generate_response(self, user_message: str) -> str:
        """Generate a coach response using the available coach service or model.

        Falls back to direct model invocation if no coach_service available.
        """
        if self.coach_service is not None:
            try:
                # CoachAgentService.chat produces a response
                # We need a simplified call for eval purposes
                result = await self.model_provider.coach_reply(
                    registry.get("eval_test_response"),
                    user_message,
                )
                return result or ""
            except Exception as exc:
                logger.warning("Response generation failed: %s", exc)
                return ""
        try:
            result = await self.model_provider.coach_reply(
                registry.get("eval_test_response"),
                user_message,
            )
            return result or ""
        except Exception as exc:
            logger.warning("Response generation failed: %s", exc)
            return ""

    # ----------------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------------

    def _persist_run(
        self,
        run_id: uuid.UUID,
        suite_name: str,
        model_used: str,
        prompt_version: str | None,
        total_cases: int,
        passed_count: int,
        average_score: float,
        dimension_averages: dict[str, float],
        started_at: datetime,
        completed_at: datetime,
        results: list[dict[str, Any]],
    ) -> models.EvalRun:
        """Save an evaluation run and all individual results to the database."""
        eval_run = models.EvalRun(
            id=run_id,
            suite_name=suite_name,
            model_used=model_used,
            prompt_version=prompt_version,
            total_cases=total_cases,
            passed_count=passed_count,
            average_score=average_score,
            dimension_averages=dimension_averages,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.db.add(eval_run)
        self.db.flush()

        for r in results:
            result_record = models.EvalResult(
                eval_run_id=run_id,
                score=r["score"],
                passed=r["passed"],
                details={"summary": r.get("summary", ""), "case_name": r.get("case_name", "")},
                dimension_scores_json=r.get("dimensions"),
                rule_checks_json=r.get("rule_checks"),
                judge_model=model_used,
                input_message=r.get("case_name", ""),
            )
            self.db.add(result_record)

        self.db.commit()
        logger.info(
            "Persisted eval run '%s': %d/%d passed (avg %.2f)",
            suite_name, passed_count, total_cases, average_score,
        )
        return eval_run

    # ----------------------------------------------------------------
    # Utilities
    # ----------------------------------------------------------------

    def _load_json_cases(self) -> list[dict[str, Any]]:
        """Load eval cases from the JSON file."""
        from fast_api.app.services.fitness_knowledge import KNOWLEDGE_DIR
        path = KNOWLEDGE_DIR / "eval_cases.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        logger.warning("eval_cases.json not found at %s", path)
        return []

    @staticmethod
    def _parse_dimensions(
        raw: str | list[str] | None,
    ) -> list[EvalDimension] | None:
        """Parse dimension specifications from eval case config."""
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = [raw]
        valid = {d.value for d in EvalDimension}
        result = []
        for name in raw:
            if name in valid:
                result.append(EvalDimension(name))
        return result if result else None

    # ----------------------------------------------------------------
    # Coach service integration: evaluate an agent run
    # ----------------------------------------------------------------

    async def evaluate_agent_run(
        self,
        user_message: str,
        agent_context: dict[str, Any],
        coach_response: str,
    ) -> MetricsResult:
        """Evaluate a full agent run including retrieval quality.

        This wraps evaluate() with additional context for faithfulness scoring.
        """
        context_text = None
        knowledge = agent_context.get("knowledge_context", {})
        if knowledge:
            combined = []
            for item in knowledge.get("matched_explanations", []):
                combined.append(item.get("content", ""))
            if combined:
                context_text = "\n".join(combined)

        return await self.evaluate(
            user_message=user_message,
            coach_response=coach_response,
            context=context_text,
        )
