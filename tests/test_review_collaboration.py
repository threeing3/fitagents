"""Synthetic scripted tests; no live-model effectiveness claims."""

import asyncio
import json

import pytest

from algorithm.evaluation.review_collaboration_eval import compare_review_case
from fast_api.app.services.review_collaboration import ReviewCollaboration
from tests.test_domain_subagents import Provider, collect, final, reader, result

PACKET = {"intent": "weekly_review"}


def test_analysis_domains_planner_are_separate_and_handoff_is_isolated():
    provider = Provider([final(["active"])] * 5)
    rows = result(asyncio.run(collect(ReviewCollaboration(provider, reader), PACKET)))
    assert [row["domain"] for row in rows] == [
        "evidence_analysis",
        "training",
        "nutrition",
        "recovery",
        "plan_planning",
    ]
    assert all(row["status"] == "completed" for row in rows)
    analyst = json.loads(provider.messages[0][-1].content)
    planner = json.loads(provider.messages[-1][-1].content)
    assert analyst["handoff"] == []
    assert len(planner["handoff"]) == 4
    assert "observations" not in str(planner["handoff"])
    assert "recent_training" in analyst["records"]
    assert all("observations" not in row for row in rows)


def test_failed_analysis_blocks_downstream_calls():
    provider = Provider([{"action": "read", "tool": "plan.write"}])
    entries = asyncio.run(collect(ReviewCollaboration(provider, reader), PACKET))
    assert provider.calls == 1
    assert len(result(entries)) == 1
    assert entries[-2]["status"] == "blocked"


def test_failed_domain_prevents_planning_but_retains_valid_analysis():
    provider = Provider([final(), final(), {"action": "read", "tool": "approval.write"}, final()])
    rows = result(asyncio.run(collect(ReviewCollaboration(provider, reader), PACKET)))
    assert provider.calls == 4
    assert "plan_planning" not in {row["domain"] for row in rows}
    assert rows[0]["status"] == "completed"
    assert rows[2]["status"] == "failed"


def test_record_reference_and_current_plan_are_available_to_analysis():
    def with_record(role, tool):
        value = reader(role, tool)
        if tool == "records.read":
            value["recent_training"] = [{"id": "record-1", "duration": 20}]
        if tool == "constraints.read":
            value["active_plan"] = {"id": "plan-1", "revision": 2}
        return value

    provider = Provider([final(["record-1"])] * 5)
    rows = result(asyncio.run(collect(ReviewCollaboration(provider, with_record), PACKET)))
    assert all(row["status"] == "completed" for row in rows)
    assert (
        json.loads(provider.messages[0][-1].content)["constraints"]["active_plan"]["revision"] == 2
    )


def test_failed_review_cannot_generate_plan_from_user_policy_alone():
    from fast_api.app.services.coach_agent import CoachAgentService

    service = object.__new__(CoachAgentService)
    assert not service._should_generate_plan_for_context(
        {
            "review_collaboration_status": "failed",
            "current_request_policy": {"should_generate_plan": True},
        }
    )
    with pytest.raises(ValueError, match="Review dependencies failed"):
        service._generate_plan_tool(None, context_packet={"review_collaboration_status": "failed"})


def test_old_analysis_invalidated_even_if_domain_evidence_remains_current():
    provider = Provider([final()] * 5)
    reads = 0

    def changing(role, tool):
        nonlocal reads
        value = reader(role, tool)
        if role == "evidence_analysis" and tool == "records.read":
            reads += 1
            if reads >= 4:
                value["recent_training"] = [{"duration": 20}]
        return value

    entries = asyncio.run(collect(ReviewCollaboration(provider, changing), PACKET))
    assert provider.calls < 5
    assert all(row["status"] == "failed" for row in result(entries))
    assert all("recommendations" not in row for row in result(entries))


def test_offline_shows_all_five_roles_without_model_claim():
    provider = Provider(live=False)
    rows = result(asyncio.run(collect(ReviewCollaboration(provider, reader), PACKET)))
    assert len(rows) == 5
    assert all(row["status"] == "skipped" for row in rows)
    assert provider.calls == 0


def test_safety_task_cannot_enter_review_collaboration():
    packet = {"intent": "weekly_review", "secondary_intents": ["injury_or_risk"]}
    provider = Provider([final()] * 5)
    assert result(asyncio.run(collect(ReviewCollaboration(provider, reader), packet))) == []
    assert provider.calls == 0


def test_three_arm_comparison_preserves_identity_and_leaves_quality_unscored():
    async def scripted_workflow(packet, message, **limits):
        assert limits == {"max_calls": 15, "timeout_seconds": 90}
        return {"results": [{"status": "completed"}], "model_calls": 0, "model_called": False}

    reports = asyncio.run(
        compare_review_case(
            {"case_id": "synthetic-review-001", "packet": PACKET, "message": "复盘，不修改计划"},
            lambda arm: Provider([final()] * 5),
            lambda arm: reader,
            scripted_workflow,
        )
    )
    assert [row["arm"] for row in reports] == ["workflow", "single_agent", "multi_agent"]
    assert [row["model_calls"] for row in reports] == [0, 1, 5]
    assert all(row["case_id"] == "synthetic-review-001" for row in reports)
    assert all(row["semantic_task_success"] is None for row in reports)


def test_workflow_failure_preserved_without_aborting_other_arms_or_inventing_usage():
    async def failing(packet, message, **limits):
        raise RuntimeError("private failure")

    reports = asyncio.run(
        compare_review_case(
            {"case_id": "failure", "packet": PACKET, "message": "复盘"},
            lambda arm: Provider([final()] * 5),
            lambda arm: reader,
            failing,
        )
    )
    assert len(reports) == 3
    assert reports[0]["execution_error"] == "workflow_failed"
    assert reports[0]["model_calls"] is None
    assert reports[0]["usage_known"] is False
    assert reports[0]["protocol_completed"] is False
    assert reports[1]["protocol_completed"] and reports[2]["protocol_completed"]
    assert "private failure" not in str(reports)


def test_default_baseline_runs_actual_reflection_builders():
    reports = asyncio.run(
        compare_review_case(
            {
                "case_id": "default-baseline",
                "packet": {**PACKET, "review_window": {"start": "2026-09-21", "end": "2026-09-27"}},
                "message": "复盘",
            },
            lambda arm: Provider(live=False),
            lambda arm: reader,
        )
    )
    assert reports[0]["protocol_completed"]
    assert reports[0]["model_calls"] == 0
    assert reports[0]["results"][0]["domain"] == "existing_weekly_reflection"
    assert reports[0]["semantic_task_success"] is None


def test_timeout_cancels_workflow_and_other_arms_remain_reported():
    closed = []

    async def waiting(packet, message, **limits):
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    reports = asyncio.run(
        compare_review_case(
            {"case_id": "timeout", "packet": PACKET, "message": "复盘"},
            lambda arm: Provider(live=False),
            lambda arm: reader,
            waiting,
            timeout_seconds=0.03,
        )
    )
    assert closed == [True]
    assert reports[0]["execution_error"] == "timeout"
    assert reports[0]["model_calls"] is None
    assert len(reports) == 3


def test_agent_timeout_counts_inflight_call_and_closes_the_model_wait():
    closed = []

    class WaitingProvider(Provider):
        async def ainvoke(self, messages):
            self.calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    async def workflow(packet, message, **limits):
        return {"results": [], "model_calls": 0, "model_called": False}

    reports = asyncio.run(
        compare_review_case(
            {"case_id": "agent-timeout", "packet": PACKET, "message": "复盘"},
            lambda arm: WaitingProvider(),
            lambda arm: reader,
            workflow,
            timeout_seconds=0.03,
        )
    )
    assert len(closed) == 2
    for report in reports[1:]:
        assert report["protocol_completed"] is False
        assert report["model_calls"] == 1
        assert report["model_called"] is True
        assert report["semantic_task_success"] is None
