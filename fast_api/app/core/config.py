from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the AI fitness coach platform."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    background_task_max_attempts: int = Field(default=3, alias="BACKGROUND_TASK_MAX_ATTEMPTS")

    llm_provider: Literal["qwen", "deepseek", "openai", "offline"] = Field(
        default="qwen", alias="LLM_PROVIDER"
    )
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="QWEN_BASE_URL",
    )
    qwen_chat_model: str = Field(default="qwen-plus", alias="QWEN_CHAT_MODEL")
    qwen_embedding_model: str = Field(
        default="text-embedding-v4", alias="QWEN_EMBEDDING_MODEL"
    )
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")

    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_chat_model: str = Field(
        default="deepseek-v4-pro", alias="DEEPSEEK_CHAT_MODEL"
    )
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")

    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

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
    use_llm_driven_agent: bool = Field(
        default=False, alias="USE_LLM_DRIVEN_AGENT"
    )
    agent_runtime_mode: Literal["auto", "llm_driven", "code_driven"] = Field(
        default="auto", alias="AGENT_RUNTIME_MODE"
    )
    code_driven_planner: Literal["llm", "rule"] = Field(
        default="llm", alias="CODE_DRIVEN_PLANNER"
    )
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

    @property
    def has_live_model_key(self) -> bool:
        if self.llm_provider == "qwen":
            return bool(self.dashscope_api_key)
        if self.llm_provider == "deepseek":
            return bool(self.deepseek_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return False

    @property
    def chat_base_url(self) -> str | None:
        if self.llm_provider == "qwen":
            return self.qwen_base_url
        if self.llm_provider == "deepseek":
            return self.deepseek_base_url
        if self.llm_provider == "openai":
            return self.openai_base_url
        return None

    @property
    def chat_api_key(self) -> str | None:
        if self.llm_provider == "qwen":
            return self.dashscope_api_key
        if self.llm_provider == "deepseek":
            return self.deepseek_api_key
        if self.llm_provider == "openai":
            return self.openai_api_key
        return None

    @property
    def chat_model(self) -> str:
        if self.llm_provider == "qwen":
            return self.qwen_chat_model
        if self.llm_provider == "deepseek":
            return self.deepseek_chat_model
        if self.llm_provider == "openai":
            return self.openai_chat_model
        return "offline-rule-engine"

    @property
    def embedding_model(self) -> str:
        if self.embedding_provider == "qwen":
            return self.qwen_embedding_model
        if self.embedding_provider == "openai":
            return self.openai_embedding_model
        return "offline-hash-embedding"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
