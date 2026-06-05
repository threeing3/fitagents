from types import SimpleNamespace

from fast_api.app.services.memory_conflict_resolver import MemoryConflictResolver


def test_injury_correction_targets_shoulder_terms():
    resolver = MemoryConflictResolver(db=None)

    targets = resolver._injury_targets("shoulder", "我的右肩没有伤")

    assert "shoulder" in targets
    assert "右肩" in targets


def test_memory_matches_injury_context_only():
    resolver = MemoryConflictResolver(db=None)
    shoulder_risk = SimpleNamespace(
        memory_type="risk_signal",
        category="injury",
        content="用户有 shoulder injury 风险",
        summary=None,
        memory_metadata={"tags": ["injury"]},
    )
    normal_training = SimpleNamespace(
        memory_type="training_performance",
        category=None,
        content="用户今天卧推55kg做组",
        summary=None,
        memory_metadata={},
    )

    assert resolver._memory_matches_injury(shoulder_risk, ["shoulder"])
    assert not resolver._memory_matches_injury(normal_training, ["shoulder"])
