"""Evaluation-only local Qwen transport; never reads configured API credentials."""

import asyncio
import json
import urllib.request

from langchain_core.messages import AIMessage


class LocalQwenModel:
    def __init__(self):
        self.calls = 0
        self.transport = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    async def ainvoke(self, messages):
        if self.calls >= 40:
            raise RuntimeError("Local evaluation call limit reached")
        roles = {"system": "system", "human": "user", "ai": "assistant"}
        body = {
            "model": "Qwen3-4B-Q4_K_M.gguf",
            "messages": [{"role": roles[m.type], "content": m.content} for m in messages],
            "temperature": 0,
            "seed": 42,
            "max_tokens": 512,
            "stream": False,
        }
        self.calls += 1

        def invoke():
            request = urllib.request.Request(
                "http://127.0.0.1:8079/v1/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with self.transport.open(request, timeout=60) as response:
                raw = json.loads(response.read().decode("utf-8"))
            usage = raw["usage"]
            return AIMessage(
                content=raw["choices"][0]["message"]["content"],
                usage_metadata={
                    "input_tokens": usage["prompt_tokens"],
                    "output_tokens": usage["completion_tokens"],
                    "total_tokens": usage["total_tokens"],
                },
                response_metadata={
                    "model_name": raw.get("model", body["model"]),
                    "finish_reason": raw["choices"][0]["finish_reason"],
                    "raw_response": raw,
                },
            )

        return await asyncio.to_thread(invoke)
