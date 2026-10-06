"""Validate explicit background attempt references before publishing a link."""

import uuid

from fast_api.app.db import models


def background_trace_reference(db, job):
    if job is None:
        return None
    try:
        identity = uuid.UUID(str((job.payload_json or {}).get("execution_trace_run_id")))
    except (ValueError, TypeError):
        return None
    run = db.get(models.AgentRun, identity)
    if run is None or run.user_id != job.user_id or run.run_type != "background.task":
        return None
    nodes = run.nodes or []
    if not any(
        isinstance(node, dict)
        and node.get("type") == "BackgroundTaskReference"
        and node.get("task_id") == str(job.id)
        and node.get("attempt") == job.attempts
        for node in nodes
    ):
        return None
    markers = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "DurableStreamJournal"
    ]
    if len(markers) != 1 or markers[0].get("journal_id") != str(identity):
        return None
    return str(identity)
