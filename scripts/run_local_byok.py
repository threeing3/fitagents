"""Run the original local app with user credentials, never inherited model keys."""

import argparse
import getpass
import os
import secrets
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Provider = Literal["qwen", "deepseek", "offline"]
OFFICIAL_ENDPOINTS = {
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com",
}


@dataclass(frozen=True)
class LocalModelAccess:
    provider: Provider
    model: str = ""
    api_key: str = field(default="", repr=False)


def build_environment(access: LocalModelAccess, database_url: str, port: int) -> dict[str, str]:
    """Process-only overrides: no network calls, file writes or secret logging."""
    if access.provider not in (*OFFICIAL_ENDPOINTS, "offline"):
        raise ValueError("Unsupported provider")
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("Use a dedicated PostgreSQL database with the psycopg driver")
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    if access.provider != "offline" and (not access.model.strip() or not access.api_key.strip()):
        raise ValueError("Live mode requires your own non-empty model name and API key")
    if access.provider == "offline" and access.api_key:
        raise ValueError("Offline mode must not receive credentials")
    overrides = {
        "DATABASE_URL": database_url,
        "LLM_PROVIDER": access.provider,
        "DASHSCOPE_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "",
        "LANGSMITH_API_KEY": "",
        "LANGCHAIN_API_KEY": "",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
        "EMBEDDING_PROVIDER": "offline",
        "USE_PGVECTOR": "false",
        "ENVIRONMENT": "development",
        "DEMO_MODE": "false",
        "INVITE_CODE": "",
        "REDIS_URL": "",
        "ADAPTER_INFERENCE_URL": "",
        "ADAPTER_INFERENCE_KEY": "",
        "VISION_MODEL": "",
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "AUTH_COOKIE_SECURE": "false",
        "CORS_ORIGINS": f"http://127.0.0.1:{port}",
        "AGENT_RUNTIME_MODE": "code_driven",
        "USE_LLM_DRIVEN_AGENT": "false",
        "CODE_DRIVEN_PLANNER": "rule" if access.provider == "offline" else "llm",
        "EVAL_LLM_JUDGE_ENABLED": "false",
        "DAILY_MODEL_CALL_LIMIT": "20",
        "GLOBAL_DAILY_MODEL_LIMIT": "20",
        "QWEN_BASE_URL": OFFICIAL_ENDPOINTS["qwen"],
        "DEEPSEEK_BASE_URL": OFFICIAL_ENDPOINTS["deepseek"],
    }
    if access.provider == "qwen":
        overrides.update(DASHSCOPE_API_KEY=access.api_key, QWEN_CHAT_MODEL=access.model.strip())
    elif access.provider == "deepseek":
        overrides.update(DEEPSEEK_API_KEY=access.api_key, DEEPSEEK_CHAT_MODEL=access.model.strip())
    return overrides


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["qwen", "deepseek", "offline"], required=True)
    parser.add_argument("--model", default="", help="Model ID available in your provider account")
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--ack-db-migrations", action="store_true")
    args = parser.parse_args(argv)
    if not args.ack_db_migrations:
        parser.error("Startup migrates the database; --ack-db-migrations is required")
    if args.provider != "offline" and not args.model.strip():
        parser.error("Live mode requires --model; no inherited model setting is used")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    return args


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        raise SystemExit("Run from the repository root")
    if not (root / "web/dist/index.html").is_file():
        raise SystemExit("Build the original frontend first: cd web, npm ci, npm run build")
    print("Local-only startup migrates the selected database. Use a new project database.")
    try:
        database_url = hidden_input("Your dedicated database URL (hidden): ").strip()
        key = "" if args.provider == "offline" else hidden_input("Your own API key (hidden): ")
        overrides = build_environment(
            LocalModelAccess(args.provider, args.model, key.strip()), database_url, args.port
        )
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Startup cancelled; no service started") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    os.environ.update(overrides)
    # Import only after overrides: Settings and dotenv must not select an old model key.
    import uvicorn

    print(f"Open http://127.0.0.1:{args.port}/; provider={args.provider}; worker not auto-started.")
    uvicorn.run("fast_api.app.main:app", host="127.0.0.1", port=args.port, reload=False)


def hidden_input(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise ValueError("Secure input unavailable; use an interactive terminal") from None


if __name__ == "__main__":
    main()
