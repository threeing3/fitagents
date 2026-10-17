"""Pressure-driven session surface compaction; raw chat rows remain immutable."""

import asyncio
import copy
import uuid

from sqlalchemy import select

from fast_api.app.db import models
from fast_api.app.services.context_compaction import (
    CompactionPolicy,
    ContextCompactor,
    measure_units,
    prune_tool_result,
)
from fast_api.app.services.context_compaction_store import (
    CompactionBusy,
    ContextCompactionStore,
    conversation_checkpoint,
)
from fast_api.app.services.context_window_manager import MODEL_WINDOWS


def session_surface(db, user_id, session_id):
    rows = list(
        db.scalars(
            select(models.ChatMessage)
            .where(
                models.ChatMessage.user_id == user_id,
                models.ChatMessage.session_id == session_id,
            )
            .order_by(models.ChatMessage.created_at, models.ChatMessage.id)
            .execution_options(populate_existing=True)
        )
    )
    checkpoint = conversation_checkpoint(db, user_id, session_id)
    cutoff = checkpoint.get("through_message_id")
    if cutoff:
        position = next((i for i, row in enumerate(rows) if str(row.id) == cutoff), None)
        if position is None:
            raise ValueError("Checkpoint source boundary is missing; refusing to hide history")
        rows = rows[position + 1 :]
    surface = [{"id": str(row.id), "role": row.role, "content": row.content} for row in rows]
    return [checkpoint, *surface] if checkpoint else surface


def compaction_policy(model_name):
    window = MODEL_WINDOWS.get(model_name, MODEL_WINDOWS["unknown"])
    return CompactionPolicy(window, max(1200, int(window * 0.15)), min(65536, int(window * 0.1)))


async def _prepare_session_context_once(
    db, provider, user_id, session_id, current_message, *, manual=False
):
    session = db.get(models.ConversationSession, session_id)
    if session is None or session.user_id != user_id:
        raise PermissionError("Conversation session owner mismatch")
    if not provider.has_live_model():
        return {"status": "skipped", "reason": "live_summarizer_unavailable"}
    surface = session_surface(db, user_id, session_id)
    current = {"id": "current_request", "role": "user", "content": current_message}
    # The new request is retained, never used as evidence of an already saved action.
    messages = [*surface, current]
    policy = compaction_policy(provider.settings.chat_model)
    store = ContextCompactionStore(
        db.get_bind(), "conversation:" + str(session_id), user_id, session_id
    )
    store.revision, _ = store.read()
    snapshot = copy.deepcopy(surface)

    async def emit(event):
        if event["type"] == "compaction/start":
            event = {
                **event,
                "input_messages": snapshot,
                "count_method": "utf8_bytes",
                "model": provider.settings.chat_model,
            }
        await store.emit(event)

    async def install(ids, checkpoint):
        # Reload actual source rows to reject in-place corrections during summarization.
        for row in list(db.identity_map.values()):
            if isinstance(row, models.ContextCheckpointState):
                db.expire(row)
        fresh = session_surface(db, user_id, session_id)
        old_selected = [item for item in snapshot if item["id"] in ids]
        new_selected = [item for item in fresh if item["id"] in ids]
        if old_selected != new_selected:
            raise CompactionBusy("Selected source span changed during summarization")
        through = next(
            (item["id"] for item in reversed(old_selected) if not item.get("context_checkpoint")),
            None,
        )
        if through is None:
            raise ValueError("No new original messages selected")
        uuid.UUID(through)
        checkpoint["through_message_id"] = through
        await store.install(
            ids,
            checkpoint,
            source_rows=[item for item in old_selected if not item.get("context_checkpoint")],
        )

    async def summarize(request):
        try:
            return await asyncio.wait_for(provider.summarize_context(request), timeout=45)
        except Exception as error:
            raise ValueError("Auxiliary compaction call failed") from error

    try:
        checkpoint = await ContextCompactor(policy).compact(
            messages,
            request_units=measure_units(messages),
            summarize=summarize,
            emit=emit,
            install=install,
            trigger="manual" if manual else "pressure",
        )
        # Expire only our independent row. Never overwrite caller business identity/state.
        for row in list(db.identity_map.values()):
            if isinstance(row, models.ContextCheckpointState):
                db.expire(row)
        return {"status": "completed" if checkpoint else "skipped", "checkpoint": checkpoint}
    except (CompactionBusy, ValueError, TimeoutError) as error:
        return {"status": "failed", "reason": type(error).__name__}


async def prepare_session_context(
    db, provider, user_id, session_id, current_message, *, manual=False
):
    last = None
    try:
        async with asyncio.timeout(90):
            for round_index in range(16):
                result = await _prepare_session_context_once(
                    db,
                    provider,
                    user_id,
                    session_id,
                    current_message,
                    manual=manual and round_index == 0,
                )
                if result["status"] != "completed":
                    return last if last and result["status"] == "skipped" else result
                last = result
    except TimeoutError:
        return {
            "status": "failed",
            "reason": "compaction_total_deadline",
            "partial_checkpoint_installed": bool(last),
        }
    return {**(last or {}), "round_limit_reached": True}


async def compact_runtime_context(
    db, provider, user_id, session_id, run_id, messages, *, trigger="pressure"
):
    """Single-run tool surface: paired protocol, persistent input and replacement."""
    # Offline diagnostics have no durable sink. Never install an unrecorded summary;
    # the existing final budget admission remains responsible for rejecting overflow.
    if db is None or not callable(getattr(provider, "summarize_context", None)):
        return messages
    session = db.get(models.ConversationSession, session_id)
    if session is None or session.user_id != user_id:
        raise PermissionError("Runtime compaction owner mismatch")
    policy = compaction_policy(provider.settings.chat_model)
    if measure_units(messages) >= policy.threshold_units:
        views = [
            prune_tool_result(item, max(200, min(12000, policy.window_units // 4))) or item
            for item in messages
        ]
        if views != messages:
            pruning = ContextCompactionStore(
                db.get_bind(), "runtime:" + str(run_id), user_id, session_id
            )
            pruning.revision, _ = pruning.read()
            operation = str(uuid.uuid4())
            base = {"compaction_id": operation, "trigger": "tool-pruning"}
            await pruning.emit(
                {**base, "type": "compaction/start", "input_messages": copy.deepcopy(messages)}
            )
            status, committed = "failed", False
            try:
                await pruning.install(
                    [item["id"] for item in messages],
                    {"id": operation, "context_view": views},
                )
                messages = views
                committed, status = True, "completed"
            finally:
                await pruning.emit(
                    {**base, "type": "compaction/end", "status": status, "installed": committed}
                )
    if trigger == "pressure" and measure_units(messages) < policy.threshold_units:
        return messages
    store = ContextCompactionStore(db.get_bind(), "runtime:" + str(run_id), user_id, session_id)
    store.revision, _ = store.read()
    snapshot = copy.deepcopy(messages)
    installed = None

    async def emit(event):
        if event["type"] == "compaction/start":
            event = {**event, "input_messages": snapshot, "count_method": "utf8_bytes"}
        await store.emit(event)

    async def install(ids, checkpoint):
        nonlocal installed
        if messages != snapshot:
            raise CompactionBusy("Runtime source surface changed")
        await store.install(ids, checkpoint)
        positions = [i for i, item in enumerate(messages) if item["id"] in ids]
        installed = [*messages[: positions[0]], checkpoint, *messages[positions[-1] + 1 :]]

    async def summarize(request):
        try:
            return await asyncio.wait_for(provider.summarize_context(request), timeout=45)
        except Exception as error:
            raise ValueError("Auxiliary compaction call failed") from error

    try:
        await ContextCompactor(policy).compact(
            messages,
            request_units=measure_units(messages),
            summarize=summarize,
            emit=emit,
            install=install,
            trigger=trigger,
        )
    except (CompactionBusy, ValueError, TimeoutError):
        pass  # Keep the original or already durably installed surface; final admission still checks.
    return installed if installed is not None else messages
