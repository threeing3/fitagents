"""Scoped model-boundary callbacks; bounded inputs and public outputs only."""

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from langchain_core.callbacks import BaseCallbackHandler

_journal = ContextVar("fitagent_model_journal", default=None)
_origin = ContextVar("fitagent_model_origin", default=None)
_attempts = ContextVar("fitagent_model_attempts", default=None)


@contextmanager
def model_origin(details):
    # Caller is the trusted child runtime, never a model-supplied action argument.
    token = _origin.set(
        {
            key: details[key]
            for key in (
                "parent_id",
                "child_id",
                "domain",
                "activation",
                "model_iteration",
                "step_id",
            )
            if key in details
        }
    )
    attempts = []
    attempts_token = _attempts.set(attempts)
    try:
        yield
    except BaseException as exc:
        for recorder, call_id in attempts:
            try:
                recorder.local_interrupt(call_id, exc)
            except (OSError, ValueError):
                pass  # Preserve cancellation/error; missing terminal remains unknown.
        raise
    finally:
        _attempts.reset(attempts_token)
        _origin.reset(token)


def recorded_read(tool_name, reader, *, phase="observe"):
    journal = _journal.get()
    if journal is None:
        return reader()
    origin = dict(_origin.get() or {})
    step_id = str(uuid.uuid4())
    started = time.perf_counter()
    journal.append(
        {
            "event_id": str(uuid.uuid4()),
            "name": "subagent.read.start",
            "type": "subagent.read.start",
            "status": "running",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "details": {**origin, "step_id": step_id, "tool_name": tool_name, "read_phase": phase},
        }
    )
    try:
        observation = reader()
    except BaseException as exc:
        journal.append(
            {
                "event_id": str(uuid.uuid4()),
                "name": "subagent.read.end",
                "type": "subagent.read.end",
                "status": "failed",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "details": {
                    **origin,
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "read_phase": phase,
                    "error_type": type(exc).__name__,
                },
            }
        )

        raise

    journal.append(
        {
            "event_id": str(uuid.uuid4()),
            "name": "subagent.read.end",
            "type": "subagent.read.end",
            "status": "completed",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "details": {
                **origin,
                "step_id": step_id,
                "tool_name": tool_name,
                "read_phase": phase,
                "observation": observation,
            },
        }
    )
    return observation


@contextmanager
def model_recording(journal):
    token = _journal.set(journal)
    try:
        yield
    finally:
        _journal.reset(token)


def record_child_state(parent_id, row):
    journal = _journal.get()
    if journal is None:
        return
    journal.append(
        {
            "event_id": str(uuid.uuid4()),
            "name": "subagent.lifecycle",
            "type": "subagent.lifecycle",
            "status": row["status"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "summary": f"子任务状态更新：{row['status']}；运行时观察，不代表业务提交。",
            "details": {
                "parent_id": str(parent_id),
                **{
                    key: row.get(key)
                    for key in (
                        "child_id",
                        "domain",
                        "activation",
                        "revision",
                        "mode",
                        "failure_reason",
                    )
                },
                "evidence_kind": "runtime_observation",
            },
        }
    )


def record_auxiliary_call(name, status, details, input_summary=None, *, latency_ms=0):
    journal = _journal.get()
    if journal is None:
        return
    journal.append(
        {
            "event_id": str(uuid.uuid4()),
            "name": name,
            "type": name,
            "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "input_summary": input_summary or {},
            "latency_ms": latency_ms,
            "details": {**dict(_origin.get() or {}), **details},
        }
    )


def public_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for block in content:
            if isinstance(block, str):
                result.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                result.append({"type": "text", "text": block.get("text", "")})
            elif isinstance(block, dict) and block.get("type") in {
                "image_url",
                "image",
                "input_image",
            }:
                result.append({"type": "image", "media_omitted": True})
        return result
    return {"unsupported_content_type": type(content).__name__}


class ModelCallRecorder(BaseCallbackHandler):
    # Execute before transport, in the caller context. Failed persistence must not
    # silently produce a live model request with no start record.
    run_inline = True
    raise_error = True

    def __init__(self, provider, model, purpose):
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self.calls = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        journal = _journal.get()
        if journal is None:
            return
        origin = dict(_origin.get() or {})
        self.calls[str(run_id)] = (journal, time.perf_counter(), origin)
        journal.append(
            {
                "event_id": str(uuid.uuid4()),
                "type": "model.start",
                "name": "model.start",
                "status": "running",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "input_summary": {
                    "message_batches": [
                        [
                            {
                                "role": message.type,
                                "content": public_content(message.content),
                                "tool_calls": getattr(message, "tool_calls", []),
                                "tool_call_id": getattr(message, "tool_call_id", None),
                            }
                            for message in batch
                        ]
                        for batch in messages
                    ],
                    "exact_request": False,
                    "tool_definitions": (kwargs.get("invocation_params") or {}).get("tools", []),
                    "parameters": {
                        key: (kwargs.get("invocation_params") or {}).get(key)
                        for key in ("temperature", "max_tokens", "response_format")
                    },
                },
                "details": {
                    **origin,
                    "model_call_id": str(run_id),
                    "provider": self.provider,
                    "model": self.model,
                    "purpose": self.purpose,
                    "parent_model_call_id": str(parent_run_id) if parent_run_id else None,
                },
            }
        )

        attempts = _attempts.get()
        if attempts is not None:
            attempts.append((self, str(run_id)))

    def local_interrupt(self, run_id, error):
        call = self.calls.pop(str(run_id), None)
        if call is None:
            return
        journal, started, origin = call
        journal.append(
            {
                "event_id": str(uuid.uuid4()),
                "name": "model.interrupted",
                "type": "model.interrupted",
                "status": "outcome_unknown",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "summary": "本地模型等待已中断；远端执行结果未确认。",
                "details": {
                    **origin,
                    "model_call_id": str(run_id),
                    "local_stop_reason": type(error).__name__,
                    "remote_result_confirmed": False,
                },
            }
        )

    def _finish(self, run_id, status, details):
        call = self.calls.pop(str(run_id), None)
        if call is None:
            return
        journal, started, origin = call
        journal.append(
            {
                "event_id": str(uuid.uuid4()),
                "type": "model.end",
                "name": "model.end",
                "status": status,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "details": {**origin, "model_call_id": str(run_id), **details},
            }
        )

    def on_llm_end(self, response, *, run_id, **kwargs):
        generations = []
        for batch in response.generations:
            for generation in batch:
                message = getattr(generation, "message", None)
                generations.append(
                    {
                        "content": public_content(
                            message.content if message is not None else generation.text
                        ),
                        "tool_calls": getattr(message, "tool_calls", []),
                        "usage": getattr(message, "usage_metadata", None),
                    }
                )
        self._finish(run_id, "completed", {"public_outputs": generations})

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(run_id, "failed", {"error_type": type(error).__name__})
