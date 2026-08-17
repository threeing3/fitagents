import base64
import json
import logging
import uuid
from typing import Iterable

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from fast_api.app.core.config import Settings, get_settings
from fast_api.app.core.prompts import registry
from fast_api.app.core.retry import retry_with_backoff
from fast_api.app.services.usage_quota import UsageQuotaService

logger = logging.getLogger(__name__)


class ModelProvider:
    """Provider abstraction for Qwen, DeepSeek, OpenAI, and offline fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        user_id: uuid.UUID | None = None,
        endpoint: str | None = None,
        quota_service: UsageQuotaService | None = None,
    ):
        self.settings = settings or get_settings()
        self.user_id = user_id
        self.endpoint = endpoint
        self.quota_service = quota_service or UsageQuotaService(self.settings)
        self.quota_exhausted = False

    def has_live_model(self) -> bool:
        if not self.settings.has_live_model_key:
            return False
        snapshot = self.quota_service.snapshot(self.user_id)
        self.quota_exhausted = not snapshot.live_calls_available
        return snapshot.live_calls_available

    def _reserve_live_call(self) -> bool:
        allowed = self.quota_service.reserve(
            self.user_id,
            provider=self.settings.llm_provider,
            model_name=self.settings.chat_model,
            endpoint=self.endpoint,
        )
        self.quota_exhausted = not allowed
        return allowed

    def chat_model(self, temperature: float = 0.4) -> ChatOpenAI | None:
        if not self.settings.has_live_model_key or not self._reserve_live_call():
            return None

        kwargs = {
            "model": self.settings.chat_model,
            "temperature": temperature,
            "api_key": self.settings.chat_api_key,
            "timeout": 90,
            "max_retries": 0,
            "max_tokens": 1200,
            "http_client": httpx.Client(trust_env=False, timeout=90),
            "http_async_client": httpx.AsyncClient(trust_env=False, timeout=90),
        }
        if self.settings.chat_base_url:
            kwargs["base_url"] = self.settings.chat_base_url
        return ChatOpenAI(**kwargs)

    def intent_model(self) -> ChatOpenAI | None:
        """Return a bounded, non-thinking model client for structured intent decisions."""

        if not self.settings.has_live_model_key or not self._reserve_live_call():
            return None
        kwargs = {
            "model": self.settings.chat_model,
            "temperature": 0.0,
            "api_key": self.settings.chat_api_key,
            "timeout": 45,
            "max_retries": 0,
            "max_tokens": 500,
            "http_client": httpx.Client(trust_env=False, timeout=45),
            "http_async_client": httpx.AsyncClient(trust_env=False, timeout=45),
        }
        if self.settings.chat_base_url:
            kwargs["base_url"] = self.settings.chat_base_url
        if self.settings.llm_provider == "deepseek":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return ChatOpenAI(**kwargs)

    def embeddings_model(self) -> OpenAIEmbeddings | None:
        if not self.settings.has_live_embedding_key:
            return None

        kwargs = {
            "model": self.settings.embedding_model,
            "api_key": self.settings.embedding_api_key,
            "check_embedding_ctx_length": False,
            "http_client": httpx.Client(trust_env=False, timeout=90),
        }
        if self.settings.embedding_base_url:
            kwargs["base_url"] = self.settings.embedding_base_url
        return OpenAIEmbeddings(**kwargs)

    def embedding_mode(self) -> str:
        if self.settings.has_live_embedding_key:
            return f"{self.settings.embedding_provider}:{self.settings.embedding_model}"
        return "vector_unavailable_bm25_fallback"

    # ----------------------------------------------------------------
    # Vision / multimodal
    # ----------------------------------------------------------------

    def vision_model(self) -> ChatOpenAI | None:
        """Return a ChatOpenAI instance configured for a vision-capable model.

        Defaults to gpt-4o-mini (cost-effective for food recognition).
        Falls back to the regular chat model if the provider is OpenAI-compatible.
        """
        if not self.settings.has_live_model_key or not self._reserve_live_call():
            return None

        model_name = getattr(self.settings, "vision_model", None) or "gpt-4o-mini"

        kwargs: dict = {
            "model": model_name,
            "temperature": 0.2,
            "api_key": self.settings.chat_api_key,
            "timeout": 30,
            "max_retries": 0,
            "max_tokens": 800,
            "http_client": httpx.Client(trust_env=False, timeout=30),
            "http_async_client": httpx.AsyncClient(trust_env=False, timeout=30),
        }
        if self.settings.chat_base_url:
            kwargs["base_url"] = self.settings.chat_base_url
        return ChatOpenAI(**kwargs)

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=10.0)
    async def recognize_food(
        self, image_bytes: bytes, media_type: str = "image/jpeg"
    ) -> dict | None:
        """Analyze a food photo with a vision model and return structured nutrition data.

        Args:
            image_bytes: Raw image bytes (JPEG / PNG / WebP).
            media_type: MIME type of the image.

        Returns:
            A dict with keys: food_items (list of {name, estimated_amount, calories,
            protein_g, carbs_g, fat_g, confidence}), notes, total_calories,
            total_protein_g, total_carbs_g, total_fat_g.
            Returns None when no vision model is available.
        """
        model = self.vision_model()
        if model is None:
            return None

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{media_type};base64,{image_b64}"

        system_prompt = registry.get("food_recognition")

        user_message = HumanMessage(
            content=[
                {"type": "text", "text": "Analyze this food photo and return the JSON."},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        )

        response = await model.ainvoke([SystemMessage(content=system_prompt), user_message])
        text = str(response.content)

        # Extract JSON from possible markdown wrapper
        return self._parse_food_json(text)

    def _parse_food_json(self, text: str) -> dict | None:
        """Extract and validate the food recognition JSON from model output."""
        import re

        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                logger.warning("No JSON found in vision model output: %s", text[:200])
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON from vision model output")
                return None
        if not isinstance(data, dict):
            return None
        data.setdefault("food_items", [])
        data.setdefault("notes", "")
        data.setdefault("total_calories", 0)
        data.setdefault("total_protein_g", 0)
        data.setdefault("total_carbs_g", 0)
        data.setdefault("total_fat_g", 0)
        return data

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def coach_reply(self, system_prompt: str, user_prompt: str) -> str | None:
        model = self.chat_model()
        if model is None:
            return None
        message = await model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        return str(message.content)

    async def stream_coach_reply(self, system_prompt: str, user_prompt: str):
        """Stream coach reply with retry on initial connection."""
        model = self.chat_model()
        if model is None:
            return
        max_retries = 3
        base_delay = 1.0
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                stream = model.astream(
                    [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
                )
                async for chunk in stream:
                    content = getattr(chunk, "content", "")
                    if content:
                        yield str(content)
                    break
                async for chunk in stream:
                    content = getattr(chunk, "content", "")
                    if content:
                        yield str(content)
                return
            except Exception as exc:
                last_exc = exc
                if attempt == max_retries:
                    break
                from fast_api.app.core.retry import _is_retryable

                if not _is_retryable(exc):
                    break
                import asyncio
                import random

                delay = min(base_delay * (2**attempt), 30.0)
                delay *= 0.5 + random.random()
                logger.warning(
                    "Stream retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, exc
                )
                await asyncio.sleep(delay)
        logger.error("stream_coach_reply failed after %d attempts: %s", max_retries + 1, last_exc)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("stream_coach_reply failed without a captured exception")

    def embed_text(self, text: str) -> list[float] | None:
        """Return a real provider embedding or ``None`` when vectors are unavailable.

        A digest is useful for exact cache keys but has no semantic geometry.
        Returning it as a vector would make lexical fallback look like semantic
        retrieval in offline reports, so unavailable embeddings are explicit.
        """

        model = self.embeddings_model()
        if model is not None:
            try:
                vector = model.embed_query(text)
                return self._fit_dimension(vector)
            except Exception as exc:
                logger.warning("Embedding provider unavailable; using BM25 fallback: %s", exc)
        return None

    def _fit_dimension(self, vector: Iterable[float]) -> list[float]:
        values = list(vector)
        dimension = self.settings.vector_dimension
        if len(values) == dimension:
            return values
        if len(values) > dimension:
            return values[:dimension]
        return values + [0.0] * (dimension - len(values))
