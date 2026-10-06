"""Paired execution harness. Protocol success is not semantic task success."""

import asyncio
import copy
import time
from typing import Any

from algorithm.evaluation.review_workflow_adapter import existing_review_workflow
from fast_api.app.services.domain_subagents import DomainSubagents
from fast_api.app.services.review_collaboration import ReviewCollaboration


async def compare_review_case(
    case: dict[str, Any], provider_factory, reader_factory, workflow=None, *, timeout_seconds=90
):
    """Require an actual workflow adapter; never invent a workflow/model score.

    Factories create independent owner-scoped snapshots/providers for each arm.
    The workflow adapter returns results, model_calls, and model_called. No writes
    should be exposed by the adapter. All arms receive the same task and packet.
    Calls/time ceilings match, but token/cost equality needs live usage accounting.
    """
    if not isinstance(case.get("case_id"), str) or not case["case_id"]:
        raise ValueError("A stable case_id is required")
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 90:
        raise ValueError("Invalid arm timeout")
    workflow = workflow or existing_review_workflow
    reports = []
    for arm in ("workflow", "single_agent", "multi_agent"):
        started = time.monotonic()
        packet = copy.deepcopy(case["packet"])
        message = case["message"]
        execution_error = None
        calls = None
        called = None
        results = []
        if arm == "workflow":
            try:
                outcome = await asyncio.wait_for(
                    workflow(packet, message, max_calls=15, timeout_seconds=timeout_seconds),
                    timeout=timeout_seconds,
                )
                results = outcome["results"]
                calls = outcome["model_calls"]
                called = outcome["model_called"]
            except TimeoutError:
                execution_error = "timeout"
            except Exception:
                execution_error = "workflow_failed"
        else:
            provider = provider_factory(arm)
            reader = reader_factory(arm)
            manager = (
                ReviewCollaboration(provider, reader)
                if arm == "multi_agent"
                else DomainSubagents(provider, reader)
            )
            if arm == "single_agent":
                manager.remaining_calls = 15
                manager.max_turns = 15
                manager.deadline = time.monotonic() + timeout_seconds
                stream = manager.run(packet, message, roles=["single_review"])
            else:
                manager.worker.deadline = time.monotonic() + timeout_seconds
                stream = manager.run(packet, message)

            async def consume():
                nonlocal results
                try:
                    async for entry in stream:
                        if entry["type"] == "domain_result":
                            results = entry["results"]
                finally:
                    await stream.aclose()

            try:
                await asyncio.wait_for(consume(), timeout=timeout_seconds)
            except TimeoutError:
                execution_error = "timeout"
            except Exception:
                execution_error = "agent_execution_failed"
            worker = manager if arm == "single_agent" else manager.worker
            calls = 15 - worker.remaining_calls
            called = calls > 0
        if calls is not None and (
            isinstance(calls, bool) or not isinstance(calls, int) or not 0 <= calls <= 15
        ):
            raise ValueError("Adapter exceeded or misreported the shared call budget")
        reports.append(
            {
                "case_id": case["case_id"],
                "arm": arm,
                "execution_error": execution_error,
                "usage_known": calls is not None,
                "protocol_completed": execution_error is None
                and bool(results)
                and all(row.get("status") == "completed" for row in results),
                "model_called": called,
                "model_calls": calls,
                "elapsed_seconds": time.monotonic() - started,
                "results": results,
                "semantic_task_success": None,
                "constraint_compliance": None,
                "evidence_accuracy": None,
                "cost": None,
            }
        )
    return reports
