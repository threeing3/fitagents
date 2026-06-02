from fast_api.app.services.memory_verifier import MemoryVerifier


def test_memory_verifier_rejects_injury_memory_during_correction():
    result = MemoryVerifier().verify(
        candidates=[
            {
                "memory_type": "medical_context",
                "content": "用户提到右肩伤病风险：我的右肩没有伤。",
                "importance": 0.9,
                "confidence": 0.8,
                "memory_metadata": {"category": "risk"},
            }
        ],
        corrections=[{"field": "injuries", "action": "remove", "value": "shoulder"}],
        profile_snapshot={"injuries": []},
        message="我的右肩没有伤。",
    )

    assert result.passed is False
    assert len(result.accepted_candidates) == 0
    assert len(result.rejected_candidates) == 1
    assert result.issues[0].issue_id == "contradicts_injury_correction"


def test_memory_verifier_downgrades_temporary_preference_to_recent_state():
    result = MemoryVerifier().verify(
        candidates=[
            {
                "memory_type": "stable_preference",
                "content": "用户稳定偏好：我今天很疲劳，睡眠不好。",
                "importance": 0.8,
                "confidence": 0.8,
                "memory_metadata": {"category": "preference"},
            }
        ],
        corrections=[],
        profile_snapshot={"injuries": []},
        message="我今天很疲劳，睡眠不好。",
    )

    assert result.passed is True
    assert result.accepted_candidates[0]["memory_type"] == "recent_state"
    assert "downgrade_stable_preference_to_recent_state" in result.repair_actions


def test_memory_verifier_keeps_non_injury_memories_during_injury_correction():
    result = MemoryVerifier().verify(
        candidates=[
            {
                "memory_type": "medical_context",
                "content": "用户有甲亢背景，正在服用赛治。",
                "importance": 0.95,
                "confidence": 0.9,
                "memory_metadata": {
                    "conditions": ["hyperthyroidism"],
                    "medications": ["methimazole"],
                    "safety_level": "high",
                },
            },
            {
                "memory_type": "nutrition_habit",
                "content": "用户平时不自己做饭，更适合外食和便利饮食方案。",
                "importance": 0.75,
                "confidence": 0.8,
                "memory_metadata": {"category": "nutrition"},
            },
        ],
        corrections=[{"field": "injuries", "action": "remove", "value": "shoulder"}],
        profile_snapshot={"injuries": []},
        message="我的右肩没有伤，我平时不自己做饭，甲亢在吃赛治。",
    )

    assert result.passed is True
    assert len(result.accepted_candidates) == 2
    assert len(result.rejected_candidates) == 0
