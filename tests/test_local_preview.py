from fast_api.app.core.config import get_settings
from scripts.run_local_preview import DSN, configure_local


def test_offline_preview_uses_fixed_isolated_database_and_no_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-not-a-live-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    # Capture process-only overrides so pytest's settings are not left changed.
    import os

    before = dict(os.environ)
    try:
        configure_local(offline=True)
        settings = get_settings()
        assert settings.database_url == DSN
        assert settings.llm_provider == "offline"
        assert not settings.has_live_model_key
        assert not settings.use_pgvector
    finally:
        os.environ.clear()
        os.environ.update(before)
        get_settings.cache_clear()
