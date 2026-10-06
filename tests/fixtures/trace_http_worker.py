"""Real application HTTP/auth against an existing isolated crash database."""

import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    import dotenv
    import uvicorn

    from fast_api.app.core import config

    directory = Path(sys.argv[1]).resolve()
    database = directory / "crash.sqlite"
    database_url = sys.argv[2] if len(sys.argv) > 2 else f"sqlite:///{database.as_posix()}"
    if database_url.startswith("postgresql"):
        from sqlalchemy.engine import make_url

        url = make_url(database_url)
        if (
            url.host != "127.0.0.1"
            or url.port != 15433
            or not url.database.startswith("fitagent_crash_")
        ):
            raise ValueError("Dedicated crash database only")
    elif not database.is_file():
        raise ValueError("Existing isolated fixture database required")
    # Child-process only; do not load project .env or enable remote telemetry.
    dotenv.load_dotenv = lambda *args, **kwargs: False
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    settings = config.Settings(
        _env_file=None,
        database_url=database_url,
        agent_log_dir=str(directory),
        llm_provider="offline",
        embedding_provider="offline",
        use_pgvector=False,
        redis_url=None,
        jwt_secret_key="isolated-http-crash-test-secret-only",
        openai_api_key=None,
        deepseek_api_key=None,
        dashscope_api_key=None,
        langsmith_api_key=None,
        demo_mode=False,
    )
    config.get_settings = lambda: settings
    from fast_api.app.main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    class TestServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            print(f"READY:{port}", flush=True)

    # This test covers main app routes/auth, not migration/knowledge startup.
    server = TestServer(uvicorn.Config(app, lifespan="off", log_level="error"))
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
