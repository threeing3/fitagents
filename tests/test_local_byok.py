"""Synthetic credentials only: no provider calls or database migrations."""

import getpass
import warnings

import pytest

from scripts.run_local_byok import LocalModelAccess, build_environment, hidden_input, parse_args

DATABASE = "postgresql+psycopg://synthetic:synthetic@127.0.0.1/test_byok"


@pytest.mark.parametrize("provider", ["qwen", "deepseek"])
def test_live_requires_key_and_model(provider):
    for model, key in [("test-model", ""), ("", "synthetic-key"), ("test-model", "   ")]:
        with pytest.raises(ValueError, match="own non-empty"):
            build_environment(LocalModelAccess(provider, model, key), DATABASE, 8015)


@pytest.mark.parametrize(
    "provider,key_name", [("qwen", "DASHSCOPE_API_KEY"), ("deepseek", "DEEPSEEK_API_KEY")]
)
def test_selected_key_overrides_inherited_settings(provider, key_name, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-synthetic-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "old-synthetic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-synthetic-key")
    monkeypatch.setenv("QWEN_BASE_URL", "https://untrusted.invalid")
    access = LocalModelAccess(provider, "test-model", "new-synthetic-key")
    overrides = build_environment(access, DATABASE, 8015)
    from fast_api.app.core.config import Settings

    settings = Settings(**overrides)
    assert settings.chat_api_key == "new-synthetic-key"
    assert settings.embedding_api_key is None
    assert overrides[key_name] == "new-synthetic-key"
    assert overrides["OPENAI_API_KEY"] == ""
    other = "DEEPSEEK_API_KEY" if provider == "qwen" else "DASHSCOPE_API_KEY"
    assert overrides[other] == ""
    assert settings.chat_base_url in {
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://api.deepseek.com",
    }
    assert settings.langchain_tracing_v2 == "false"
    assert "new-synthetic-key" not in repr(access)


def test_offline_clears_keys():
    overrides = build_environment(LocalModelAccess("offline"), DATABASE, 8015)
    assert overrides["LLM_PROVIDER"] == "offline"
    assert overrides["CODE_DRIVEN_PLANNER"] == "rule"
    for name in ["DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LANGSMITH_API_KEY"]:
        assert overrides[name] == ""
    with pytest.raises(ValueError, match="must not receive"):
        build_environment(LocalModelAccess("offline", api_key="synthetic"), DATABASE, 8015)


@pytest.mark.parametrize(
    "argv",
    [
        ["--provider", "offline"],
        ["--provider", "qwen", "--ack-db-migrations"],
        ["--provider", "offline", "--ack-db-migrations", "--port", "80"],
        ["--provider", "offline", "--ack-db-migrations", "--host", "0.0.0.0"],
    ],
)
def test_unsafe_or_incomplete_startup_rejected(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_valid_offline_args():
    assert parse_args(["--provider", "offline", "--ack-db-migrations"]).port == 8015


def test_bad_database_rejected_without_echoing_secret():
    with pytest.raises(ValueError) as result:
        build_environment(LocalModelAccess("offline"), "https://synthetic-secret.invalid", 8015)
    assert "synthetic-secret" not in str(result.value)


def test_no_echo_fallback(monkeypatch):
    def unavailable(prompt):
        warnings.warn("echo would be enabled", getpass.GetPassWarning)
        return "must-not-be-read"

    monkeypatch.setattr(getpass, "getpass", unavailable)
    with pytest.raises(ValueError, match="Secure input unavailable"):
        hidden_input("synthetic prompt")
