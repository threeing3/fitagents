from functools import lru_cache
from typing import Literal

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the AI fitness coach platform."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "AI Fitness Coach Agent"
    environment: str = "development"

    database_url: str = Field(
        default="postgresql+psycopg://fitness:fitness@localhost:4553/ai_fitness_agent",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout_seconds: int = Field(default=30, alias="DB_POOL_TIMEOUT_SECONDS")
    vector_dimension: int = Field(default=1024, alias="VECTOR_DIMENSION")
    use_pgvector: bool = Field(default=True, alias="USE_PGVECTOR")
    agent_log_dir: str = Field(default="logs/agent-runs", alias="AGENT_LOG_DIR")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    rate_limit_default: str = Field(default="60/minute", alias="RATE_LIMIT_DEFAULT")
    rate_limit_chat: str = Field(default="15/minute", alias="RATE_LIMIT_CHAT")
    rate_limit_plan: str = Field(default="10/minute", alias="RATE_LIMIT_PLAN")
    rate_limit_nutrition: str = Field(default="8/minute", alias="RATE_LIMIT_NUTRITION")
    rate_limit_auth_login: str = Field(default="10/minute", alias="RATE_LIMIT_AUTH_LOGIN")
    rate_limit_auth_register: str = Field(default="60/minute", alias="RATE_LIMIT_AUTH_REGISTER")
    background_task_max_attempts: int = Field(default=3, alias="BACKGROUND_TASK_MAX_ATTEMPTS")

    llm_provider: Literal["qwen", "deepseek", "openai", "offline"] = Field(
        default="qwen", alias="LLM_PROVIDER"
    )
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL",
    )
    qwen_chat_model: str = Field(default="qwen-plus", alias="QWEN_CHAT_MODEL")
    qwen_embedding_model: str = Field(default="text-embedding-v4", alias="QWEN_EMBEDDING_MODEL")
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")

    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_chat_model: str = Field(default="deepseek-v4-pro", alias="DEEPSEEK_CHAT_MODEL")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")

    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    vision_model: str | None = Field(default=None, alias="VISION_MODEL")

    # Evaluation is deterministic by default. A live judge must be enabled
    # explicitly so local regression tests never spend tokens or call a
    # provider merely because an API key is present in .env.
    eval_llm_judge_enabled: bool = Field(default=False, alias="EVAL_LLM_JUDGE_ENABLED")

    embedding_provider: Literal["qwen", "openai", "offline"] = Field(
        default="qwen", alias="EMBEDDING_PROVIDER"
    )

    langchain_tracing_v2: str = Field(default="false", alias="LANGCHAIN_TRACING_V2")
    langchain_project: str = Field(default="ai-fitness-coach", alias="LANGCHAIN_PROJECT")
    langsmith_api_key: str | None = Field(default=None, alias="LANGSMITH_API_KEY")

    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:8526",
        alias="CORS_ORIGINS",
    )

    # Legacy agent architecture toggle. Prefer AGENT_RUNTIME_MODE for new work.
    use_llm_driven_agent: bool = Field(default=False, alias="USE_LLM_DRIVEN_AGENT")
    agent_runtime_mode: Literal["auto", "llm_driven", "code_driven"] = Field(
        default="auto", alias="AGENT_RUNTIME_MODE"
    )
    code_driven_planner: Literal["llm", "rule"] = Field(default="llm", alias="CODE_DRIVEN_PLANNER")
    code_driven_planner_fallback: Literal["rule", "error"] = Field(
        default="rule", alias="CODE_DRIVEN_PLANNER_FALLBACK"
    )

    # JWT auth
    jwt_secret_key: str = Field(
        default="change-me-in-production-use-a-random-64-char-string",
        alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=1440, alias="JWT_EXPIRE_MINUTES")  # 24h
    auth_cookie_name: str = Field(default="ai_fitness_session", alias="AUTH_COOKIE_NAME")
    auth_cookie_secure: bool = Field(default=False, alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite: Literal["lax", "strict"] = Field(
        default="lax", alias="AUTH_COOKIE_SAMESITE"
    )

    # Public demo and cost controls
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    demo_email: str | None = Field(default=None, alias="DEMO_EMAIL")
    demo_password: str | None = Field(default=None, alias="DEMO_PASSWORD")
    invite_code: str | None = Field(default=None, alias="INVITE_CODE")
    daily_model_call_limit: int = Field(default=20, alias="DAILY_MODEL_CALL_LIMIT", ge=1)
    global_daily_model_limit: int = Field(default=500, alias="GLOBAL_DAILY_MODEL_LIMIT", ge=1)
    metrics_token: str | None = Field(default=None, alias="METRICS_TOKEN")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, alias="MAX_UPLOAD_BYTES", ge=1024)
    max_image_pixels: int = Field(default=25_000_000, alias="MAX_IMAGE_PIXELS", ge=1)

    # Optional adapter service is introduced now so deployments can be configured
    # before the real adapter is trained in maturity phase 4.
    adapter_inference_url: str | None = Field(default=None, alias="ADAPTER_INFERENCE_URL")
    adapter_inference_key: str | None = Field(default=None, alias="ADAPTER_INFERENCE_KEY")

    # Computed provider properties remain patchable in tests and experiments.
    # Pydantic settings otherwise exposes read-only properties, which makes it
    # impossible to simulate offline/online modes without mutating .env.
    _has_live_model_key_override: bool | None = PrivateAttr(default=None)
    _chat_base_url_override: str | None = PrivateAttr(default=None)
    _chat_base_url_override_set: bool = PrivateAttr(default=False)
    _chat_api_key_override: str | None = PrivateAttr(default=None)
    _chat_api_key_override_set: bool = PrivateAttr(default=False)
    _chat_model_override: str | None = PrivateAttr(default=None)
    _chat_model_override_set: bool = PrivateAttr(default=False)

    @property
    def has_live_model_key(self) -> bool:
        if self._has_live_model_key_override is not None:
            return self._has_live_model_key_override
        if self.llm_provider == "qwen":
            return bool(self.dashscope_api_key)
        if self.llm_provider == "deepseek":
            return bool(self.deepseek_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return False

    @has_live_model_key.setter
    def has_live_model_key(self, value: bool) -> None:
        self._has_live_model_key_override = bool(value)

    @has_live_model_key.deleter
    def has_live_model_key(self) -> None:
        self._has_live_model_key_override = None

    @property
    def chat_base_url(self) -> str | None:
        if self._chat_base_url_override_set:
            return self._chat_base_url_override
        if self.llm_provider == "qwen":
            return self.qwen_base_url
        if self.llm_provider == "deepseek":
            return self.deepseek_base_url
        if self.llm_provider == "openai":
            return self.openai_base_url
        return None

    @chat_base_url.setter
    def chat_base_url(self, value: str | None) -> None:
        self._chat_base_url_override = value
        self._chat_base_url_override_set = True

    @chat_base_url.deleter
    def chat_base_url(self) -> None:
        self._chat_base_url_override = None
        self._chat_base_url_override_set = False

    @property
    def chat_api_key(self) -> str | None:
        if self._chat_api_key_override_set:
            return self._chat_api_key_override
        if self.llm_provider == "qwen":
            return self.dashscope_api_key
        if self.llm_provider == "deepseek":
            return self.deepseek_api_key
        if self.llm_provider == "openai":
            return self.openai_api_key
        return None

    @chat_api_key.setter
    def chat_api_key(self, value: str | None) -> None:
        self._chat_api_key_override = value
        self._chat_api_key_override_set = True

    @chat_api_key.deleter
    def chat_api_key(self) -> None:
        self._chat_api_key_override = None
        self._chat_api_key_override_set = False

    @property
    def chat_model(self) -> str:
        if self._chat_model_override_set and self._chat_model_override is not None:
            return self._chat_model_override
        if self.llm_provider == "qwen":
            return self.qwen_chat_model
        if self.llm_provider == "deepseek":
            return self.deepseek_chat_model
        if self.llm_provider == "openai":
            return self.openai_chat_model
        return "offline-rule-engine"

    @chat_model.setter
    def chat_model(self, value: str) -> None:
        self._chat_model_override = value
        self._chat_model_override_set = True

    @chat_model.deleter
    def chat_model(self) -> None:
        self._chat_model_override = None
        self._chat_model_override_set = False

    @property
    def embedding_model(self) -> str:
        if self.embedding_provider == "qwen":
            return self.qwen_embedding_model
        if self.embedding_provider == "openai":
            return self.openai_embedding_model
        return "vector-unavailable"

    @property
    def embedding_base_url(self) -> str | None:
        if self.embedding_provider == "qwen":
            return self.qwen_base_url
        if self.embedding_provider == "openai":
            return self.openai_base_url
        return None

    @property
    def embedding_api_key(self) -> str | None:
        if self.embedding_provider == "qwen":
            return self.dashscope_api_key
        if self.embedding_provider == "openai":
            return self.openai_api_key
        return None

    @property
    def has_live_embedding_key(self) -> bool:
        if self.embedding_provider == "qwen":
            return bool(self.dashscope_api_key)
        if self.embedding_provider == "openai":
            return bool(self.openai_api_key)
        return False

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "staging"}

    def validate_runtime(self) -> None:
        """Reject unsafe production configuration before opening the service."""

        if not self.is_production:
            return

        errors: list[str] = []
        default_secret = "change-me-in-production-use-a-random-64-char-string"
        if self.jwt_secret_key == default_secret or len(self.jwt_secret_key) < 32:
            errors.append("JWT_SECRET_KEY must be a non-default secret with at least 32 characters")
        if not self.invite_code or len(self.invite_code) < 8:
            errors.append("INVITE_CODE must contain at least 8 characters")
        if not self.database_url.startswith("postgresql") or "localhost" in self.database_url:
            errors.append("DATABASE_URL must point to a non-local PostgreSQL database")
        if self.llm_provider == "offline" or not self.has_live_model_key:
            errors.append("a live model provider and matching API key are required")
        if not self.metrics_token or len(self.metrics_token) < 24:
            errors.append("METRICS_TOKEN must contain at least 24 characters")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true")
        if any(
            origin == "*" or "localhost" in origin or "127.0.0.1" in origin
            for origin in self.cors_origin_list
        ):
            errors.append("CORS_ORIGINS cannot contain wildcards or local origins")
        if self.demo_mode:
            if not self.demo_email:
                errors.append("DEMO_EMAIL is required when DEMO_MODE is enabled")
            if not self.demo_password or len(self.demo_password) < 10:
                errors.append("DEMO_PASSWORD must contain at least 10 characters")
        if errors:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
