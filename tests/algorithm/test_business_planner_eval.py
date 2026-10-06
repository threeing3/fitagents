import asyncio
import json

from langchain_core.messages import AIMessage

from algorithm.evaluation.business_planner_eval import CASES, evaluate_case


class ScriptedPlanner:
    async def ainvoke(self, messages):
        task = json.loads(messages[-1].content)
        tools = [tool["name"] for tool in task["available_tools"]]
        return AIMessage(
            content=json.dumps(
                {
                    "intent": "general",
                    "selected_tools": tools,
                    "tool_order": tools,
                    "write_intent": False,
                    "safety_level": "low",
                    "plan_generation_allowed": False,
                    "reasoning_summary": "Scripted plumbing check; host must validate.",
                }
            )
        )


def test_actual_chat_writes_and_planner_trace(tmp_path):
    row = asyncio.run(
        evaluate_case(CASES[0], tmp_path / "record", mode="llm", model=ScriptedPlanner())
    )
    assert row["passed"]
    assert row["model_calls"]
    assert row["fallback_turns"] == 0
    assert len(row["after"]) == len(row["before"]) + 1


def test_cancelled_followup_cannot_write(tmp_path):
    case = next(case for case in CASES if case["id"] == "cancel")
    row = asyncio.run(evaluate_case(case, tmp_path / "cancel", mode="rule"))
    assert row["passed"]
    assert row["after"] == row["before"]
    assert row["fallback_turns"] == 0
    assert all(state["mode"] == "rule" for state in row["planner_states"])
