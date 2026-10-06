"""Authenticated HTTP catalog query; metadata only, no cross-account access."""

import uuid

from fast_api.app.db import models
from fast_api.app.services.subagent_journal import SubagentJournal
from fast_api.app.services.subagent_runtime import SubagentRuntime
from tests.test_api_integration import _create_client_and_db


def test_catalog_http_scope_limits_and_cancellation():
    client, factory = _create_client_and_db()
    registered = client.post(
        "/v1/auth/register",
        json={
            "email": "catalog@example.com",
            "password": "synthetic1234",
            "display_name": "Catalog",
        },
    )
    assert registered.status_code == 201
    account = registered.json()
    owner = uuid.UUID(account["user_id"])
    headers = {"Authorization": "Bearer " + account["access_token"]}
    session, foreign = uuid.uuid4(), uuid.uuid4()
    with factory() as db:
        other = models.User(email="other-catalog@example.test", password_hash="disabled")
        db.add(other)
        db.flush()
        db.add_all(
            [
                models.ConversationSession(id=session, user_id=owner, title="Synthetic"),
                models.ConversationSession(id=foreign, user_id=other.id, title="Private"),
            ]
        )
        db.commit()
        runtime = SubagentRuntime()
        runtime.bind(str(owner), str(session))
        runtime.start("training")
        runtime.close_active("parent_cancelled")
        runtime.continuation_state = {
            "remaining_calls": 9,
            "private_marker": "PRIVATE_ADVISORY_CONTEXT",
        }
        SubagentJournal(db).save(runtime, expected_revision=0)
        db.commit()
    url = f"/v1/chat/sessions/{session}/subagents"
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["children"][0]["failure_reason"] == "parent_cancelled"
    assert "password" not in response.text
    assert "PRIVATE_ADVISORY_CONTEXT" not in response.text
    assert "_continuation" not in response.text
    detail = client.get(f"/v1/agent-runs/{runtime.parent_id}", headers=headers)
    assert detail.status_code == 200
    assert "PRIVATE_ADVISORY_CONTEXT" not in detail.text
    reconcile = url + "/" + runtime.parent_id + "/reconcile"
    assert (
        client.post(
            reconcile, headers=headers, json={"expected_revision": 1, "confirm_no_retry": True}
        ).status_code
        == 409
    )
    assert client.post(reconcile, headers=headers, json={"expected_revision": 1}).status_code == 422
    assert (
        client.post(
            reconcile, headers=headers, json={"expected_revision": True, "confirm_no_retry": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/chat/sessions/{foreign}/subagents/{runtime.parent_id}/reconcile",
            headers=headers,
            json={"expected_revision": 1, "confirm_no_retry": True},
        ).status_code
        == 404
    )
    assert client.get(url + "?limit=31", headers=headers).status_code == 422
    stop_url = url + "/" + runtime.parent_id + "/stop"
    assert client.post(stop_url, headers=headers, json={"expected_revision": 1}).status_code == 422
    assert (
        client.post(
            stop_url, headers=headers, json={"expected_revision": 1, "confirm_no_retry": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            stop_url, headers=headers, json={"expected_revision": True, "confirm_no_retry": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            stop_url, headers=headers, json={"expected_revision": 1, "confirm_no_retry": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/chat/sessions/{foreign}/subagents/{runtime.parent_id}/stop",
            headers=headers,
            json={"expected_revision": 1, "confirm_no_retry": True},
        ).status_code
        == 404
    )
    start = {"role": "training", "message": "合成只读咨询"}
    keyed = {**headers, "Idempotency-Key": "synthetic-http-key"}
    assert client.post(url, headers=headers, json=start).status_code == 422
    assert client.post(url, headers=keyed, json={**start, "role": "writer"}).status_code == 422
    assert client.post(url, headers=keyed, json={**start, "write_plan": True}).status_code == 422
    assert client.post(url, headers=keyed, json=start).status_code == 409  # SQLite unsupported.
    assert (
        client.post(f"/v1/chat/sessions/{foreign}/subagents", headers=keyed, json=start).status_code
        == 404
    )
    assert (
        client.post(
            url + "/" + runtime.parent_id + "/continue",
            headers=keyed,
            json={"message": "解释", "expected_catalog_revision": True},
        ).status_code
        == 422
    )
    status_url = f"/v1/chat/sessions/{session}/subagent-requests/status"
    assert client.get(status_url, headers=headers).status_code == 422
    receipt_reconcile = f"/v1/chat/sessions/{session}/subagent-requests/reconcile"
    assert (
        client.post(receipt_reconcile, headers=keyed, json={"expected_revision": 1}).status_code
        == 422
    )
    assert (
        client.post(
            receipt_reconcile, headers=keyed, json={"expected_revision": 1, "confirm_no_retry": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            receipt_reconcile,
            headers=keyed,
            json={"expected_revision": True, "confirm_no_retry": True},
        ).status_code
        == 422
    )
    assert (
        client.post(
            receipt_reconcile,
            headers=keyed,
            json={"expected_revision": 1, "confirm_no_retry": True},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/chat/sessions/{foreign}/subagent-requests/reconcile",
            headers=keyed,
            json={"expected_revision": 1, "confirm_no_retry": True},
        ).status_code
        == 404
    )
    cancel_url = f"/v1/chat/sessions/{session}/subagent-requests/cancel"
    assert client.post(cancel_url, headers=keyed, json={}).status_code == 422
    assert client.post(cancel_url, headers=keyed, json={"confirm_no_retry": 1}).status_code == 422
    assert (
        client.post(cancel_url, headers=headers, json={"confirm_no_retry": True}).status_code == 422
    )
    assert (
        client.post(cancel_url, headers=keyed, json={"confirm_no_retry": True}).status_code == 409
    )
    assert (
        client.post(
            f"/v1/chat/sessions/{foreign}/subagent-requests/cancel",
            headers=keyed,
            json={"confirm_no_retry": True},
        ).status_code
        == 404
    )
    assert client.get(status_url, headers=keyed).json() == {"status": "not_found"}
    with factory() as db:
        db.add(
            models.IdempotencyRecord(
                user_id=owner,
                operation="subagent_turn",
                idempotency_key="synthetic-http-key",
                status="processing",
                request_json={"session_id": str(session)},
                response_json={"parent_id": runtime.parent_id},
            )
        )
        db.commit()
    assert client.get(status_url, headers=keyed).json() == {
        "status": "unconfirmed",
        "parent_id": runtime.parent_id,
        "no_automatic_retry": True,
    }
    assert (
        client.get(
            f"/v1/chat/sessions/{foreign}/subagent-requests/status", headers=keyed
        ).status_code
        == 404
    )
    assert client.get(f"/v1/chat/sessions/{foreign}/subagents", headers=headers).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401
    assert (
        client.post(
            cancel_url,
            json={"confirm_no_retry": True},
            headers={"Idempotency-Key": "synthetic-http-key"},
        ).status_code
        == 401
    )
    assert (
        client.get(status_url, headers={"Idempotency-Key": "synthetic-http-key"}).status_code == 401
    )
