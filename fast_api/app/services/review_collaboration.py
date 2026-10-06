"""Evidence-first review delegation with read-only, freshness-checked handoffs."""

import copy
from collections.abc import AsyncIterator
from typing import Any

from fast_api.app.services.domain_subagents import DomainSubagents, select_domains
from fast_api.app.services.execution_events import execution_event
from fast_api.app.services.model_call_records import model_origin, recorded_read


class ReviewCollaboration:
    """Share one budget across analysis, domain advice, and candidate planning."""

    def __init__(self, provider, reader):
        self.worker = DomainSubagents(provider, reader)
        self.worker.remaining_calls = 15

    async def run(self, packet: dict[str, Any], message: str) -> AsyncIterator[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        snapshots: list[tuple[dict[str, Any], dict[str, Any]]] = []

        def evidence_changed():
            try:
                for origin, observations in snapshots:
                    with model_origin(origin):
                        for tool, value in observations.items():
                            if (
                                recorded_read(
                                    tool,
                                    lambda: self.worker.reader(origin["domain"], tool),
                                    phase="handoff_recheck",
                                )
                                != value
                            ):
                                return True
                return False
            except Exception:
                # A failed refresh is not permission to use an unchecked old result.
                return True

        review = bool(
            {packet.get("intent"), *packet.get("secondary_intents", [])}
            & {"weekly_review", "monthly_review"}
        )
        if not review or not select_domains(packet):
            yield {"type": "domain_result", "results": []}
            return
        stages = [["evidence_analysis"], select_domains(packet), ["plan_planning"]]
        for stage in stages:
            # Offline mode still displays the whole intended chain, explicitly as skipped.
            live = self.worker.provider.has_live_model()
            changed = evidence_changed()
            if live and (changed or any(row["status"] != "completed" for row in results)):
                yield execution_event(
                    "subagent.handoff",
                    "blocked",
                    "交接停止：证据已变化或前置子任务失败；保留原计划，等待重新复盘。",
                    details={
                        "failure_reason": "evidence_changed" if changed else "dependency_failed"
                    },
                )
                break
            accepted = copy.deepcopy([row for row in results if row["status"] == "completed"])
            stream = self.worker.run(packet, message, roles=stage, handoff=accepted)
            try:
                async for entry in stream:
                    if entry["type"] == "domain_result":
                        for row in entry["results"]:
                            snapshot = row.pop("observations", {})
                            if snapshot:
                                snapshots.append(
                                    (
                                        {
                                            key: row[key]
                                            for key in (
                                                "parent_id",
                                                "child_id",
                                                "domain",
                                                "activation",
                                            )
                                        },
                                        snapshot,
                                    )
                                )
                            results.append(row)
                    elif entry.get("name") != "subagent.aggregate":
                        yield entry
            finally:
                await stream.aclose()
        # Recheck all dependencies, including analysis, after the planner completes.
        changed = evidence_changed()
        if changed:
            for row in results:
                if row["status"] == "completed":
                    self.worker.runtime.invalidate(row["child_id"])
                    row["status"] = "failed"
                    row["failure_reason"] = "evidence_changed"
                    for key in ("summary", "recommendations", "uncertainties", "evidence_ids"):
                        row.pop(key, None)
                    yield execution_event(
                        "subagent.result",
                        "failed",
                        "证据已被纠正，先前建议不再有效。",
                        details={
                            key: row[key]
                            for key in (
                                "child_id",
                                "parent_id",
                                "mode",
                                "domain",
                                "label",
                                "depth",
                                "failure_reason",
                            )
                        },
                    )
            yield execution_event(
                "subagent.handoff", "blocked", "复盘证据在交付前发生变化，全部建议作废。"
            )
        yield execution_event(
            "subagent.aggregate",
            "completed"
            if results and all(row["status"] == "completed" for row in results)
            else "skipped"
            if results and all(row["status"] == "skipped" for row in results)
            else "blocked",
            "复盘协作结束；候选方案不是审批，不直接修改计划。",
            details={
                "completed": sum(row["status"] == "completed" for row in results),
                "total": len(results),
                "remaining_calls": self.worker.remaining_calls,
            },
        )
        yield {"type": "domain_result", "results": results}
