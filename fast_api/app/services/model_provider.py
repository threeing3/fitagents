import base64
import hashlib
import json
import logging
from typing import Iterable

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from fast_api.app.core.config import Settings, get_settings
from fast_api.app.core.prompts import registry
from fast_api.app.core.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class ModelProvider:
    """Provider abstraction for Qwen, DeepSeek, OpenAI, and offline fallback."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def has_live_model(self) -> bool:
        return self.settings.has_live_model_key

    def chat_model(self, temperature: float = 0.4) -> ChatOpenAI | None:
        if not self.settings.has_live_model_key:
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
        return "offline_fallback"

    # ----------------------------------------------------------------
    # Vision / multimodal
    # ----------------------------------------------------------------

    def vision_model(self) -> ChatOpenAI | None:
        """Return a ChatOpenAI instance configured for a vision-capable model.

        Defaults to gpt-4o-mini (cost-effective for food recognition).
        Falls back to the regular chat model if the provider is OpenAI-compatible.
        """
        if not self.settings.has_live_model_key:
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
    async def recognize_food(self, image_bytes: bytes, media_type: str = "image/jpeg") -> dict | None:
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

        user_message = HumanMessage(content=[
            {"type": "text", "text": "Analyze this food photo and return the JSON."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])

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
                import asyncio, random
                delay = min(base_delay * (2 ** attempt), 30.0)
                delay *= 0.5 + random.random()
                logger.warning("Stream retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, exc)
                await asyncio.sleep(delay)
        logger.error("stream_coach_reply failed after %d attempts: %s", max_retries + 1, last_exc)
        raise last_exc

    def embed_text(self, text: str) -> list[float]:
        model = self.embeddings_model()
        if model is not None:
            try:
                vector = model.embed_query(text)
                return self._fit_dimension(vector)
            except Exception:
                return self._offline_embedding(text)
        return self._offline_embedding(text)

    def _offline_embedding(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = []
        for i in range(self.settings.vector_dimension):
            raw = digest[i % len(digest)]
            values.append((raw / 255.0) - 0.5)
        return values

    def _fit_dimension(self, vector: Iterable[float]) -> list[float]:
        values = list(vector)
        dimension = self.settings.vector_dimension
        if len(values) == dimension:
            return values
        if len(values) > dimension:
            return values[:dimension]
        return values + [0.0] * (dimension - len(values))
