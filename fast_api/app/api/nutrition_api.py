"""Nutrition API — food photo recognition and meal logging."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.nutrition_service import NutritionService

nutrition_router = APIRouter(prefix="/v1/nutrition", tags=["nutrition"])
limiter = Limiter(key_func=get_remote_address)


def get_nutrition_service(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> NutritionService:
    return NutritionService(db)


# ---- Analyze food photo ----

@nutrition_router.post("/recognize")
@limiter.limit("10/minute")
async def recognize_food_photo(
    image: UploadFile,
    request: Request,
    service: NutritionService = Depends(get_nutrition_service),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Analyze a food photo using a vision model (GPT-4o).

    Upload a JPEG, PNG, or WebP image. Returns structured food data
    with estimated calories, macros, and portion sizes.

    The result is NOT persisted automatically — use POST /v1/nutrition/meals/save
    to save after reviewing/editing.
    """
    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    media_type = image.content_type or "image/jpeg"
    if media_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type: {media_type}. Supported: JPEG, PNG, WebP.",
        )

    # Read image bytes (limit to 10 MB)
    image_bytes = await image.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large. Maximum 10 MB.")

    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty image file.")

    result = await service.analyze_food_photo(
        current_user.id, image_bytes, media_type,
    )
    return result


# ---- Save meal from analysis ----

@nutrition_router.post("/meals/save")
@limiter.limit("20/minute")
def save_meal_from_analysis(
    analysis: dict[str, Any],
    request: Request,
    service: NutritionService = Depends(get_nutrition_service),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist a food analysis result as NutritionLog entries.

    Pass the full analysis dict returned by POST /v1/nutrition/recognize.
    Set ``corrected: true`` if the user edited the estimates.
    """
    if not analysis.get("food_items"):
        raise HTTPException(status_code=400, detail="No food items to save.")

    corrected = analysis.pop("corrected", False)
    service.save_meal_from_analysis(
        current_user.id, analysis, corrected_by_user=corrected,
    )
    return {
        "status": "saved",
        "items_saved": len(analysis.get("food_items", [])),
        "corrected_by_user": corrected,
    }


# ---- List meals ----

@nutrition_router.get("/meals")
def list_meals(
    days: int = Query(default=7, ge=1, le=90, description="Days of history"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List recent nutrition log entries."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)

    logs = db.scalars(
        select(models.NutritionLog)
        .where(
            models.NutritionLog.user_id == current_user.id,
            models.NutritionLog.log_date >= cutoff,
        )
        .order_by(desc(models.NutritionLog.log_date), desc(models.NutritionLog.created_at))
    ).all()

    return [
        {
            "id": str(log.id),
            "log_date": str(log.log_date),
            "meal_type": log.meal_type,
            "food_name": log.food_name,
            "estimated_amount": log.estimated_amount,
            "calories": log.calories,
            "protein_g": log.protein_g,
            "carbs_g": log.carbs_g,
            "fat_g": log.fat_g,
            "source_type": log.source_type,
            "confidence_score": log.confidence_score,
            "corrected_by_user": log.corrected_by_user,
            "image_id": str(log.image_id) if log.image_id else None,
            "created_at": str(log.created_at),
        }
        for log in logs
    ]


# ---- Daily summary ----

@nutrition_router.get("/summary")
def daily_summary(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Get nutrition daily summaries."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)

    summaries = db.scalars(
        select(models.NutritionDailySummary)
        .where(
            models.NutritionDailySummary.user_id == current_user.id,
            models.NutritionDailySummary.summary_date >= cutoff,
        )
        .order_by(desc(models.NutritionDailySummary.summary_date))
    ).all()

    return [
        {
            "id": str(s.id),
            "summary_date": str(s.summary_date),
            "total_calories": s.total_calories,
            "total_protein_g": s.total_protein_g,
            "total_carbs_g": s.total_carbs_g,
            "total_fat_g": s.total_fat_g,
            "target_calories": s.target_calories,
            "target_protein_g": s.target_protein_g,
            "adherence_score": s.adherence_score,
            "summary_text": s.summary_text,
        }
        for s in summaries
    ]
