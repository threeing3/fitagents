from types import SimpleNamespace

from fast_api.app.services.coach_agent import CoachAgentService


USER_PROFILE_MARKDOWN = """
# 个人基本信息档案 (Profile Data)

## 1. 基础人口学特征
- **性别：** 男
- **年龄：** 21 岁
- **身高：** 178 cm
- **体重：** 80 kg (160斤)

## 2. 健康与生理数据
- **静息心率：** 55 bpm
- **日常活动量：** 每日步数 6000+
- **睡眠习惯：** 约 8 小时/天
- **既往病史/特殊生理状态：** 自身免疫性甲状腺异常（偏桥本/Graves背景，甲亢）
  - 当前用药：赛治（甲巯咪唑）每日半片，辅以护肝药
  - 最新生化指标：TSH 3.686 (正常), FT3 5.53 (正常), FT4 16.22 (正常), TPOAb >1300 (极高), TgAb 11.10 (升高)

## 3. 运动与身体塑形目标
- **当前核心目标：** 12-16周内健康降脂，显现腹肌；维持/增加线条感。
- **训练频率：** 每周可训练 5 天（健身房器械齐全）。
- **当前力量水平：** 卧推：50 kg；引体向上：正手 1 个；坐姿推肩：单手 20 kg。
"""


def test_medical_context_memory_is_extracted_from_open_text():
    service = CoachAgentService(db=None)

    candidates = service._memory_candidates_from_message("我有甲亢，现在还在吃药，训练时心率容易高。")

    medical = [item for item in candidates if item["memory_type"] == "medical_context"]
    assert medical
    assert medical[0]["importance"] >= 0.9
    assert medical[0]["memory_metadata"]["medication_mentioned"] is True
    assert "hyperthyroidism" in medical[0]["memory_metadata"]["conditions"]
    assert medical[0]["memory_metadata"]["requires_medical_boundary"] is True


def test_nutrition_and_preference_memories_are_open_ended():
    service = CoachAgentService(db=None)

    candidates = service._memory_candidates_from_message("我不喜欢跑步，也不吃牛肉，乳糖不耐受。")
    types = {item["memory_type"] for item in candidates}

    assert "stable_preference" in types
    assert "nutrition_habit" in types


def test_markdown_profile_extraction_does_not_turn_shoulder_press_into_injury():
    service = CoachAgentService(db=None)

    extraction = service._rule_profile_extraction(USER_PROFILE_MARKDOWN)
    patch = extraction["profile_patch"]

    assert patch["age"] == 21
    assert patch["sex"] == "male"
    assert patch["height_cm"] == 178
    assert patch["weight_kg"] == 80
    assert patch["goal"] == "fat_loss"
    assert patch["workout_frequency"] == 5
    assert "gym" in patch["equipment_available"]
    assert "machines" in patch["equipment_available"]
    assert "barbell" in patch["equipment_available"]
    assert patch.get("injuries") is None
    assert extraction["ignored_candidates"][0]["reason"] == "training_movement_not_injury"


def test_compact_chinese_profile_extraction_for_live_chat():
    service = CoachAgentService(db=None)

    extraction = service._rule_profile_extraction(
        "我21岁，男，178cm，80kg，目标减脂，系统训练过1年，在健身房训练，每周5天。"
    )
    patch = extraction["profile_patch"]

    assert patch["age"] == 21
    assert patch["sex"] == "male"
    assert patch["height_cm"] == 178
    assert patch["weight_kg"] == 80
    assert patch["goal"] == "fat_loss"
    assert patch["experience_level"] == "intermediate"
    assert patch["workout_frequency"] == 5
    assert "gym" in patch["equipment_available"]


def test_profile_correction_removes_false_shoulder_injury_and_writes_correction_memory():
    service = CoachAgentService(db=None)
    profile = SimpleNamespace(injuries=["shoulder"], target_calories=None)
    extraction = service._rule_profile_extraction("我的右肩没有伤！！！ 我什么都可以吃 但是平时不自己做饭")
    captured = []

    def fake_write_memory(**kwargs):
        captured.append(kwargs)
        return "memory-id"

    service._write_memory = fake_write_memory
    service._apply_profile_extraction(profile, extraction)
    written = service.write_memories_from_message(
        "user-id",
        "我的右肩没有伤！！！ 我什么都可以吃 但是平时不自己做饭",
        extraction,
    )

    assert profile.injuries == []
    assert "eat_out" in extraction["profile_patch"]["dietary_preferences"]
    assert written == ["memory-id", "memory-id"]
    assert captured[0]["memory_type"] == "correction"
    assert captured[1]["memory_type"] == "nutrition_habit"


def test_failed_transcript_replay_keeps_profile_consistent():
    service = CoachAgentService(db=None)
    profile = SimpleNamespace(
        age=None,
        sex=None,
        height_cm=None,
        weight_kg=None,
        activity_level="moderate",
        goal=None,
        experience_level=None,
        workout_frequency=None,
        workout_duration=None,
        dietary_preferences=[],
        allergies=[],
        equipment_available=[],
        injuries=[],
    )

    for message in [
        USER_PROFILE_MARKDOWN,
        "我今天应该干什么？",
        "我没有说过我有肩伤",
        "系统训练过1年左右 我在健身房锻炼",
        "我的右肩没有伤！！！ 我什么都可以吃 但是平时不自己做饭",
    ]:
        extraction = service._rule_profile_extraction(message)
        service._apply_profile_extraction(profile, extraction)

    assert profile.age == 21
    assert profile.height_cm == 178
    assert profile.weight_kg == 80
    assert profile.goal == "fat_loss"
    assert profile.experience_level == "intermediate"
    assert "gym" in profile.equipment_available
    assert "eat_out" in profile.dietary_preferences
    assert profile.injuries == []
    assert service.missing_onboarding_slots(profile) == []


def test_llm_patch_cannot_overwrite_rule_frequency_with_free_text():
    service = CoachAgentService(db=None)
    extraction = {
        "profile_patch": {"goal": "fat_loss", "workout_frequency": 5},
        "corrections": [],
        "ignored_candidates": [],
    }

    service._merge_llm_profile_patch(
        extraction,
        {
            "profile_patch": {
                "goal": "weight loss",
                "workout_frequency": "12-16 times per month",
            }
        },
        "Train 5 days per week. Goal is 12-16 weeks of fat loss.",
    )

    assert extraction["profile_patch"]["goal"] == "fat_loss"
    assert extraction["profile_patch"]["workout_frequency"] == 5


def test_profile_patch_normalization_rejects_invalid_frequency_text():
    service = CoachAgentService(db=None)
    profile = SimpleNamespace(workout_frequency=None, goal=None, target_calories=None)
    extraction = {
        "profile_patch": {
            "goal": "weight loss",
            "workout_frequency": "12-16 times per month",
        },
        "corrections": [],
    }

    service._apply_profile_extraction(profile, extraction)

    assert profile.goal == "fat_loss"
    assert profile.workout_frequency is None


# ---- Height extraction: coverage for formats users actually type ----

def test_height_extraction_chinese_no_cm_suffix():
    """'身高175' without 'cm' suffix — the most common real-world input."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("我身高175，体重70kg")
    assert extraction["profile_patch"]["height_cm"] == 175


def test_height_extraction_chinese_with_colon_no_cm():
    """'身高：168' — colon separator, no cm."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("身高：168，女，25岁")
    assert extraction["profile_patch"]["height_cm"] == 168


def test_height_extraction_english_meters():
    """'1.75m' — English meter format, should convert to 175 cm."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("I am 1.75m tall, weight 80kg")
    assert extraction["profile_patch"]["height_cm"] == 175


def test_height_extraction_chinese_colloquial_meters():
    """'1米75' — Chinese colloquial meter format, should convert to 175 cm."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("我1米75，体重80公斤")
    # "1米75" → "1.75" → 175
    assert extraction["profile_patch"]["height_cm"] == 175


def test_height_extraction_with_cm_suffix_still_works():
    """Existing '178cm' format should still work (regression check)."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("身高178cm，体重80kg")
    assert extraction["profile_patch"]["height_cm"] == 178


def test_training_load_kg_is_not_extracted_as_body_weight():
    service = CoachAgentService(db=None)

    extraction = service._rule_profile_extraction(
        "今天练胸，我尝试了卧推55KG做组，做了3x5组，然后50kg做2x8组，上胸采用固定器械。"
    )

    assert "weight_kg" not in extraction["profile_patch"]
    ignored_weights = [
        item
        for item in extraction["ignored_candidates"]
        if item.get("field") == "weight_kg"
    ]
    assert ignored_weights
    assert {item["candidate"] for item in ignored_weights} == {55.0, 50.0}
    assert all(item["reason"] == "training_load_not_body_weight" for item in ignored_weights)


def test_llm_weight_patch_rejected_when_source_text_only_mentions_training_load():
    service = CoachAgentService(db=None)
    extraction = {"profile_patch": {}, "corrections": [], "ignored_candidates": []}

    service._merge_llm_profile_patch(
        extraction,
        {"profile_patch": {"weight_kg": 55}},
        "今天卧推55kg做组，后面50kg做2x8组。",
    )

    assert "weight_kg" not in extraction["profile_patch"]


def test_llm_weight_patch_accepted_when_source_text_mentions_body_weight():
    service = CoachAgentService(db=None)
    extraction = {"profile_patch": {}, "corrections": [], "ignored_candidates": []}

    service._merge_llm_profile_patch(
        extraction,
        {"profile_patch": {"weight_kg": 80}},
        "我现在体重80kg，卧推55kg做组。",
    )

    assert extraction["profile_patch"]["weight_kg"] == 80


def test_height_extraction_rejects_out_of_range():
    """Values like '5000' (clearly not a height) should be rejected."""
    service = CoachAgentService(db=None)
    extraction = service._rule_profile_extraction("身高5000cm")
    assert "height_cm" not in extraction["profile_patch"]


def test_height_extraction_normalize_converts_meters_in_patch():
    """_normalize_profile_patch_value should also handle meter values (defense-in-depth)."""
    service = CoachAgentService(db=None)
    result = service._normalize_profile_patch_value("height_cm", "1.75")
    assert result == 175


def test_height_extraction_normalize_rejects_implausible_values():
    """Values that don't look like height in any unit should be None."""
    service = CoachAgentService(db=None)
    assert service._normalize_profile_patch_value("height_cm", "abc") is None
    assert service._normalize_profile_patch_value("height_cm", "0") is None
    assert service._normalize_profile_patch_value("height_cm", "300") is None
