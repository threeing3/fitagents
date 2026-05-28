"""Tests for the centralized prompt registry."""
import pytest

from fast_api.app.core.prompts import PromptRegistry, registry


# ---- Registry loading ----

def test_registry_loads_all_prompts():
    ids = registry.list_ids()
    assert len(ids) == 17, f"Expected 17 prompts, got {len(ids)}"


def test_registry_get_returns_content():
    text = registry.get("coach_onboarding")
    assert "AI personal fitness coach" in text
    assert len(text) > 50


def test_registry_get_raises_keyerror_for_unknown():
    with pytest.raises(KeyError):
        registry.get("nonexistent_prompt_id")


def test_registry_get_or_default_returns_default():
    result = registry.get_or_default("nonexistent", "fallback_value")
    assert result == "fallback_value"


def test_registry_get_or_default_returns_real_value():
    result = registry.get_or_default("coach_onboarding", "fallback")
    assert "AI personal fitness coach" in result


def test_registry_version():
    assert registry.version("coach_onboarding") == "1.0"
    assert registry.version("food_recognition") == "1.0"


def test_registry_description():
    desc = registry.description("food_recognition")
    assert "food photo" in desc.lower()


def test_registry_info_returns_full_metadata():
    info = registry.info("coach_coaching_reply")
    assert info["id"] == "coach_coaching_reply"
    assert info["version"] == "1.0"
    assert "coaching" in info["description"].lower()
    assert "last_modified" in info


def test_registry_all_have_versions():
    for pid in registry.list_ids():
        ver = registry.version(pid)
        assert ver, f"{pid} has no version"
        assert "." in ver, f"{pid} version '{ver}' doesn't look like semver"


def test_registry_all_have_non_empty_content():
    for pid in registry.list_ids():
        text = registry.get(pid)
        assert text, f"{pid} has empty content"
        assert len(text.strip()) > 5, f"{pid} content too short: {len(text)} chars"


# ---- Key prompts exist ----

def test_coach_prompts_exist():
    for pid in [
        "coach_profile_extractor",
        "coach_onboarding",
        "coach_onboarding_stream",
        "coach_coaching_reply",
        "coach_coaching_reply_stream",
    ]:
        assert pid in registry.list_ids(), f"Missing prompt: {pid}"


def test_food_recognition_prompt_exists():
    text = registry.get("food_recognition")
    assert "nutrition" in text.lower()
    assert "JSON" in text


def test_eval_prompts_exist():
    text = registry.get("eval_judge")
    assert "SAFETY" in text
    assert "RELEVANCE" in text
    assert "ACCURACY" in text
    assert "overall_score" in text


def test_guardrail_prompts_exist():
    for pid in ["guardrail_block_medical", "guardrail_block_dangerous", "guardrail_block_generic"]:
        text = registry.get(pid)
        assert len(text) > 10, f"{pid} too short"


def test_fallback_prompts_exist():
    for pid in ["fallback_local_coaching", "fallback_safety_reply", "fallback_onboarding"]:
        text = registry.get(pid)
        assert len(text) > 10, f"{pid} too short or missing"


# ---- Template substitution ----

def test_fallback_local_coaching_template():
    tmpl = registry.get("fallback_local_coaching")
    result = tmpl.format(
        goal="fat_loss",
        equipment="gym",
        exercises="- squat: 3x10",
        calories=1800,
        protein=120,
        carb=180,
        fat=50,
        medical_note="",
    )
    assert "fat_loss" in result
    assert "1800" in result
    assert "gym" in result
    assert "squat: 3x10" in result


def test_fallback_onboarding_template():
    tmpl = registry.get("fallback_onboarding")
    result = tmpl.format(known_text=" (估算目标热量 2000 kcal)", needed="年龄, 身高")
    assert "年龄" in result
    assert "身高" in result


def test_error_model_call_template():
    tmpl = registry.get("error_model_call")
    result = tmpl.format(provider="OpenAI", model="gpt-4o", error="timeout")
    assert "OpenAI" in result
    assert "gpt-4o" in result
    assert "timeout" in result


# ---- Reload ----

def test_reload_does_not_throw():
    registry.reload()
    assert len(registry.list_ids()) == 17


# ---- Custom path ----

def test_custom_prompts_path(tmp_path):
    import yaml
    custom_yaml = tmp_path / "prompts.yaml"
    data = {
        "prompts": {
            "custom_test": {
                "version": "2.0",
                "description": "A custom test prompt",
                "content": "Hello from custom registry",
            }
        }
    }
    with open(custom_yaml, "w") as f:
        yaml.dump(data, f, allow_unicode=True)

    custom_reg = PromptRegistry(str(custom_yaml))
    assert custom_reg.get("custom_test") == "Hello from custom registry"
    assert custom_reg.version("custom_test") == "2.0"
