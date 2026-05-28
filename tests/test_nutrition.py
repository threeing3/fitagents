"""Tests for food photo recognition and nutrition services."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.nutrition_service import NutritionService


# ----------------------------------------------------------------
# ModelProvider._parse_food_json unit tests
# ----------------------------------------------------------------

def test_parse_valid_food_json():
    provider = ModelProvider()
    result = provider._parse_food_json(
        '{"food_items": [{"name": "Chicken breast", "calories": 200, '
        '"protein_g": 40, "carbs_g": 0, "fat_g": 4, '
        '"estimated_amount": "150g", "confidence": 0.9}], '
        '"notes": "Lean protein", "total_calories": 200, '
        '"total_protein_g": 40, "total_carbs_g": 0, "total_fat_g": 4}'
    )
    assert result is not None
    assert len(result["food_items"]) == 1
    assert result["food_items"][0]["name"] == "Chicken breast"
    assert result["food_items"][0]["calories"] == 200
    assert result["food_items"][0]["confidence"] == 0.9
    assert result["total_calories"] == 200
    assert result["notes"] == "Lean protein"


def test_parse_json_with_markdown_fences():
    provider = ModelProvider()
    result = provider._parse_food_json(
        '```json\n{"food_items": [{"name": "Rice", "calories": 300, '
        '"protein_g": 6, "carbs_g": 65, "fat_g": 1, '
        '"estimated_amount": "200g", "confidence": 0.85}], '
        '"notes": "White rice", "total_calories": 300, '
        '"total_protein_g": 6, "total_carbs_g": 65, "total_fat_g": 1}\n```'
    )
    assert result is not None
    assert len(result["food_items"]) == 1
    assert result["food_items"][0]["name"] == "Rice"


def test_parse_json_without_fences_but_extra_text():
    """JSON object embedded in conversational text should still be extracted."""
    provider = ModelProvider()
    result = provider._parse_food_json(
        'Here is the analysis:\n{"food_items": [], '
        '"notes": "No food detected", "total_calories": 0, '
        '"total_protein_g": 0, "total_carbs_g": 0, "total_fat_g": 0}\nDone.'
    )
    assert result is not None
    assert result["food_items"] == []
    assert "No food detected" in result["notes"]


def test_parse_missing_keys_get_defaults():
    provider = ModelProvider()
    result = provider._parse_food_json('{"food_items": []}')
    assert result is not None
    assert result["food_items"] == []
    assert result["notes"] == ""
    assert result["total_calories"] == 0
    assert result["total_protein_g"] == 0
    assert result["total_carbs_g"] == 0
    assert result["total_fat_g"] == 0


def test_parse_no_json_returns_none():
    provider = ModelProvider()
    result = provider._parse_food_json("Sorry, I couldn't analyze this image.")
    assert result is None


def test_parse_non_dict_json_returns_none():
    provider = ModelProvider()
    result = provider._parse_food_json('["not", "a", "dict"]')
    assert result is None


def test_parse_multiple_food_items():
    provider = ModelProvider()
    result = provider._parse_food_json(
        '{"food_items": ['
        '{"name": "Salmon", "estimated_amount": "120g", "calories": 250, '
        '"protein_g": 30, "carbs_g": 0, "fat_g": 14, "confidence": 0.9},'
        '{"name": "Broccoli", "estimated_amount": "100g", "calories": 35, '
        '"protein_g": 3, "carbs_g": 7, "fat_g": 0, "confidence": 0.95}'
        '], "notes": "Healthy meal", "total_calories": 285, '
        '"total_protein_g": 33, "total_carbs_g": 7, "total_fat_g": 14}'
    )
    assert result is not None
    assert len(result["food_items"]) == 2
    assert result["food_items"][0]["name"] == "Salmon"
    assert result["food_items"][1]["name"] == "Broccoli"


# ----------------------------------------------------------------
# ModelProvider.vision_model tests
# ----------------------------------------------------------------

def test_vision_model_returns_none_without_api_key():
    """vision_model should return None when no live API key is configured."""
    provider = ModelProvider()
    with patch.object(provider.settings, "has_live_model_key", False):
        assert provider.vision_model() is None


def test_vision_model_returns_chat_openai_with_key():
    """vision_model should return a ChatOpenAI instance when API key is present."""
    provider = ModelProvider()
    with patch.object(provider.settings, "has_live_model_key", True), \
         patch.object(provider.settings, "chat_api_key", "sk-test"), \
         patch.object(provider.settings, "chat_base_url", None):
        model = provider.vision_model()
        assert model is not None
        assert model.model_name == "gpt-4o-mini"
        assert model.temperature == 0.2


def test_vision_model_respects_custom_model_name():
    """vision_model should use a custom model name when set on settings."""
    provider = ModelProvider()
    with patch.object(provider.settings, "has_live_model_key", True), \
         patch.object(provider.settings, "chat_api_key", "sk-test"), \
         patch.object(provider.settings, "vision_model", "gpt-4o"), \
         patch.object(provider.settings, "chat_base_url", None):
        model = provider.vision_model()
        assert model.model_name == "gpt-4o"


# ----------------------------------------------------------------
# NutritionService tests
# ----------------------------------------------------------------


def test_analyze_food_photo_offline_fallback():
    """When no vision model is available, analyze_food_photo returns offline status."""
    db = MagicMock()
    service = NutritionService(db)

    with patch.object(service.model_provider, "recognize_food", new_callable=AsyncMock) as mock_rec:
        mock_rec.return_value = None  # No vision model
        result = service.db  # type: ignore[assignment]
        # Actually call the method
        ...

    # Test directly via the service
    import asyncio
    with patch.object(service.model_provider, "recognize_food", new_callable=AsyncMock) as mock_rec:
        mock_rec.return_value = None
        result = asyncio.run(service.analyze_food_photo(
            uuid.uuid4(), b"fake-image-bytes", "image/jpeg",
        ))
        assert result["status"] == "offline"
        assert result["food_items"] == []
        assert result["total_calories"] == 0


def test_analyze_food_photo_returns_structured_result():
    """When vision model succeeds, result has ok status and structured data."""
    db = MagicMock()
    service = NutritionService(db)
    import asyncio

    mock_result = {
        "food_items": [
            {"name": "Apple", "estimated_amount": "1 medium", "calories": 95,
             "protein_g": 0.5, "carbs_g": 25, "fat_g": 0.3, "confidence": 0.95},
        ],
        "notes": "Looks like a fresh apple",
        "total_calories": 95,
        "total_protein_g": 0.5,
        "total_carbs_g": 25,
        "total_fat_g": 0.3,
    }

    with patch.object(service.model_provider, "recognize_food", new_callable=AsyncMock) as mock_rec:
        mock_rec.return_value = mock_result
        result = asyncio.run(service.analyze_food_photo(
            uuid.uuid4(), b"fake-apple-image", "image/jpeg",
        ))
        assert result["status"] == "ok"
        assert result["food_items"][0]["name"] == "Apple"
        assert result["total_calories"] == 95


def test_save_meal_sets_corrected_flag():
    """save_meal_from_analysis should set corrected_by_user on all log entries."""
    db = MagicMock()
    db.scalar.return_value = None  # No existing daily summary
    service = NutritionService(db)

    analysis = {
        "food_items": [
            {"name": "Pasta", "estimated_amount": "300g", "calories": 350,
             "protein_g": 12, "carbs_g": 60, "fat_g": 8, "confidence": 0.8},
        ],
        "notes": "Homemade pasta",
        "total_calories": 350,
        "total_protein_g": 12,
        "total_carbs_g": 60,
        "total_fat_g": 8,
    }
    user_id = uuid.uuid4()

    service.save_meal_from_analysis(user_id, analysis, corrected_by_user=True)

    # Should have called db.add at least once for the food item
    assert db.add.called
    # Check the NutritionLog was created with corrected_by_user=True
    call_args = db.add.call_args[0][0]
    assert call_args.corrected_by_user is True
    assert call_args.food_name == "Pasta"
    assert call_args.source_type == "photo"


def test_save_meal_defaults_unknown_food_name():
    """Food items with no name should default to 'Unknown food'."""
    db = MagicMock()
    db.scalar.return_value = None
    service = NutritionService(db)

    analysis = {
        "food_items": [
            {"calories": 100, "protein_g": 5, "carbs_g": 10, "fat_g": 5, "confidence": 0.3},
        ],
        "notes": "",
        "total_calories": 100,
        "total_protein_g": 5,
        "total_carbs_g": 10,
        "total_fat_g": 5,
    }

    service.save_meal_from_analysis(uuid.uuid4(), analysis)
    call_args = db.add.call_args[0][0]
    assert call_args.food_name == "Unknown food"


def test_save_meal_upserts_daily_summary():
    """When a daily summary already exists, totals should be accumulated."""
    db = MagicMock()
    existing_summary = MagicMock()
    existing_summary.total_calories = 500
    existing_summary.total_protein_g = 30
    existing_summary.total_carbs_g = 50
    existing_summary.total_fat_g = 15
    existing_summary.summary_text = "Earlier meal"
    db.scalar.return_value = existing_summary
    service = NutritionService(db)

    analysis = {
        "food_items": [
            {"name": "Snack", "estimated_amount": "50g", "calories": 150,
             "protein_g": 5, "carbs_g": 20, "fat_g": 7, "confidence": 0.7},
        ],
        "notes": "Afternoon snack",
        "total_calories": 150,
        "total_protein_g": 5,
        "total_carbs_g": 20,
        "total_fat_g": 7,
    }

    service.save_meal_from_analysis(uuid.uuid4(), analysis)

    assert existing_summary.total_calories == 650  # 500 + 150
    assert existing_summary.total_protein_g == 35  # 30 + 5
    assert existing_summary.total_carbs_g == 70    # 50 + 20
    assert existing_summary.total_fat_g == 22       # 15 + 7
