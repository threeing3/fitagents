"""Conservative admission for complete text-only model requests.

UTF-8 byte units deliberately overestimate typical byte-based tokenization;
they are not provider token counts or an advertised-window verification.
"""

import json
from typing import Any

from langchain_core.messages import BaseMessage

from fast_api.app.services.context_window_manager import MODEL_WINDOWS, OUTPUT_RESERVE


class PromptBudgetExceeded(ValueError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(
            f"prompt budget exceeded: {report['input_units']} > {report['input_limit']} "
            f"({report['count_method']})"
        )


def enforce_prompt_budget(model_name: str, messages: list[BaseMessage]) -> dict[str, Any]:
    """Check the full content before client creation or quota reservation."""
    window = MODEL_WINDOWS.get(model_name, MODEL_WINDOWS["unknown"])
    reserve = max(1200, int(window * OUTPUT_RESERVE))
    payload = [{"role": message.type, "content": message.content} for message in messages]
    units = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    units += 32 * len(messages)
    report = {
        "model_name": model_name,
        "count_method": "utf8_bytes_with_message_margin",
        "input_units": units,
        "input_limit": max(0, window - reserve),
        "output_reserve": reserve,
        "model_window_known": model_name in MODEL_WINDOWS,
    }
    if units > report["input_limit"]:
        raise PromptBudgetExceeded(report)
    return report
