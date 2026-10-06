"""Vision factory protocol and synchronous embedding boundaries, no network."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from fast_api.app.core.config import Settings
from fast_api.app.services import model_provider as providers
from fast_api.app.services.durable_stream_journal import DurableStreamJournal, read_stream_journal
from fast_api.app.services.model_call_records import model_origin, model_recording


def settings():
    return Settings(
        _env_file=None, llm_provider="offline", embedding_provider="offline", vector_dimension=3
    )


def test_vision_factory_records_public_result_but_omits_image_bytes(tmp_path, monkeypatch):
    config = settings()
    config.has_live_model_key = True
    provider = providers.ModelProvider(config)
    monkeypatch.setattr(provider, "_reserve_live_call", lambda: True)

    def factory(**kwargs):
        assert kwargs["callbacks"][0].purpose == "vision"
        kwargs["http_client"].close()
        asyncio.run(kwargs["http_async_client"].aclose())
        return FakeListChatModel(
            responses=[json.dumps({"food_items": [{"name": "synthetic food"}]})],
            callbacks=kwargs["callbacks"],
        )

    monkeypatch.setattr(providers, "ChatOpenAI", factory)
    # Construct outside the running loop; recognize_food still executes its original logic.
    model = provider.vision_model()
    monkeypatch.setattr(provider, "vision_model", lambda: model)
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)

    async def run():
        with model_recording(journal):
            return await provider.recognize_food(b"private-image-payload", "image/png")

    try:
        result = asyncio.run(run())
    finally:
        journal.close()
    assert result["food_items"][0]["name"] == "synthetic food"
    entries = read_stream_journal(*identity, tmp_path)["entries"]
    assert [entry["name"] for entry in entries] == ["model.start", "model.end"]
    assert entries[0]["details"]["purpose"] == "vision"
    assert "media_omitted" in str(entries)
    assert "data:image" not in str(entries) and "private-image-payload" not in str(entries)


@pytest.mark.parametrize("mode", ["success", "failure", "offline"])
def test_embedding_records_dimensions_or_degradation_without_text_or_vectors(
    tmp_path, monkeypatch, mode
):
    provider = providers.ModelProvider(settings())
    clock = iter([10.0, 10.012])
    monkeypatch.setattr(providers, "time", SimpleNamespace(perf_counter=lambda: next(clock)))

    def query(text):
        assert text == "private semantic query"
        if mode == "failure":
            raise RuntimeError("private semantic query and credential")
        return [0.234567, 0.345678]

    monkeypatch.setattr(
        provider,
        "embeddings_model",
        lambda: None if mode == "offline" else SimpleNamespace(embed_query=query),
    )
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)
    try:
        with (
            model_recording(journal),
            model_origin({"child_id": "synthetic-child", "domain": "training"}),
        ):
            result = provider.embed_text("private semantic query")
    finally:
        journal.close()
    entries = read_stream_journal(*identity, tmp_path)["entries"]
    assert "private semantic query" not in str(entries)
    assert "0.234567" not in str(entries)
    assert all(entry["details"]["child_id"] == "synthetic-child" for entry in entries)
    if mode == "success":
        assert len(result) == 3
        assert entries[-1]["details"]["returned_dimension"] == 2
        assert entries[-1]["details"]["fitted_dimension"] == 3
    else:
        assert result is None
        assert entries[-1]["status"] == ("failed" if mode == "failure" else "skipped")
    if mode != "offline":
        assert entries[0]["details"]["model_call_id"] == entries[-1]["details"]["model_call_id"]
        assert entries[-1]["latency_ms"] == 12


def test_vision_offline_is_skipped_not_a_fake_success(tmp_path):
    provider = providers.ModelProvider(settings())
    identity = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    journal = DurableStreamJournal(*identity, tmp_path)

    async def run():
        with model_recording(journal):
            return await provider.recognize_food(b"not transmitted")

    try:
        assert asyncio.run(run()) is None
    finally:
        journal.close()
    entries = read_stream_journal(*identity, tmp_path)["entries"]
    assert len(entries) == 1 and entries[0]["status"] == "skipped"
    assert entries[0]["details"]["model_called"] is False


def test_embedding_start_persistence_failure_prevents_query(tmp_path, monkeypatch):
    provider = providers.ModelProvider(settings())
    invoked = []
    monkeypatch.setattr(
        provider,
        "embeddings_model",
        lambda: SimpleNamespace(embed_query=lambda text: invoked.append(text)),
    )
    journal = DurableStreamJournal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), tmp_path)

    def fail(entry):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(journal, "append", fail)
    try:
        with model_recording(journal), pytest.raises(OSError):
            provider.embed_text("do not transmit")
        assert invoked == []
    finally:
        journal.close()
