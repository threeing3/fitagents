import json

import pytest

from algorithm.evaluation.tool_contract_prompt_compare import LegacyPromptAgent
from fast_api.app.services.llm_agent import LLMAgentService


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "additionalProperties": False},
        {"type": "object", "properties": {}},
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"kind": {"type": "string", "enum": ["run", "swim"]}},
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    ],
)
def test_model_visible_schema_preserves_actual_input_contract(schema):
    agent = object.__new__(LLMAgentService)
    assert json.loads(agent._simplify_schema(schema)) == schema


def test_unspecified_schema_does_not_invent_closed_contract():
    agent = object.__new__(LLMAgentService)
    assert agent._simplify_schema({}) == ""


def test_legacy_control_reproduces_missing_closed_empty_contract():
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    legacy = object.__new__(LegacyPromptAgent)
    current = object.__new__(LLMAgentService)
    assert legacy._simplify_schema(schema) == ""
    assert json.loads(current._simplify_schema(schema)) == schema
