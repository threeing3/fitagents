"""Nutrition service — food photo recognition and meal logging helpers."""

import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.model_provider import ModelProvider

logger = logging.getLogger(__name__)


class NutritionService:
    """Handles food photo analysis and nutrition log persistence."""

    def __init__(self, db: Session, model_provider: ModelProvider | None = None):
        self.db = db
        self.model_provider = model_provider or ModelProvider()

    async def analyze_food_photo(
        self,
        user_id: uuid.UUID,
        image_bytes: bytes,
        media_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        """Analyze a food photo and return structured nutrition estimates.

        Does NOT persist to the database — use ``save_meal_from_analysis``
        to persist the result after the user confirms or edits it.
        """
        result = await self.model_provider.recognize_food(image_bytes, media_type)

        if result is None:
            return {
                "status": "offline",
                "message": "Food recognition requires a vision-capable model (GPT-4o). "
                           "Configure OPENAI_API_KEY in your .env file.",
                "food_items": [],
                "notes": "",
                "total_calories": 0,
                "total_protein_g": 0,
                "total_carbs_g": 0,
                "total_fat_g": 0,
            }

        result["status"] = "ok"
        result["message"] = "Food analyzed successfully. Review and confirm to save."
        return result

    def save_meal_from_analysis(
        self,
        user_id: uuid.UUID,
        analysis: dict[str, Any],
        image_id: uuid.UUID | None = None,
        corrected_by_user: bool = False,
    ) -> models.NutritionLog:
        """Persist a food recognition result as NutritionLog entries.

        Creates one NutritionLog row per food item for granular tracking.
        """
        food_items = analysis.get("food_items") or []
        log_date = date.today()

        for item in food_items:
            self.db.add(
                models.NutritionLog(
                    user_id=user_id,
                    log_date=log_date,
                    meal_type=None,  # Can be inferred or set later
                    food_name=str(item.get("name") or "Unknown food"),
                    estimated_amount=str(item.get("estimated_amount") or ""),
                    calories=item.get("calories"),
                    protein_g=item.get("protein_g"),
                    carbs_g=item.get("carbs_g"),
                    fat_g=item.get("fat_g"),
                    source_type="photo",
                    confidence_score=item.get("confidence", 0.5),
                    image_id=image_id,
                    corrected_by_user=corrected_by_user,
                )
            )

        # Upsert daily summary
        self._upsert_daily_summary(user_id, log_date, analysis)

        self.db.commit()
        logger.info(
            "Saved %d food items from photo for user %s", len(food_items), user_id,
        )
        return food_items  # type: ignore[return-value]

    def _upsert_daily_summary(
        self,
        user_id: uuid.UUID,
        summary_date: date,
        analysis: dict[str, Any],
    ) -> None:
        """Create or update the daily nutrition summary."""
        existing = self.db.scalar(
            __import__("sqlalchemy").select(models.NutritionDailySummary).where(
                models.NutritionDailySummary.user_id == user_id,
                models.NutritionDailySummary.summary_date == summary_date,
            )
        )
        if existing:
            existing.total_calories = (existing.total_calories or 0) + (analysis.get("total_calories") or 0)
            existing.total_protein_g = (existing.total_protein_g or 0) + (analysis.get("total_protein_g") or 0)
            existing.total_carbs_g = (existing.total_carbs_g or 0) + (analysis.get("total_carbs_g") or 0)
            existing.total_fat_g = (existing.total_fat_g or 0) + (analysis.get("total_fat_g") or 0)
            existing.summary_text = analysis.get("notes") or existing.summary_text
        else:
            self.db.add(
                models.NutritionDailySummary(
                    user_id=user_id,
                    summary_date=summary_date,
                    total_calories=analysis.get("total_calories") or 0,
                    total_protein_g=analysis.get("total_protein_g") or 0,
                    total_carbs_g=analysis.get("total_carbs_g") or 0,
                    total_fat_g=analysis.get("total_fat_g") or 0,
                    summary_text=analysis.get("notes") or "",
                )
            )
        self.db.flush()
