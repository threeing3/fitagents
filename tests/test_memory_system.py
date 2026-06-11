from fast_api.app.services.bm25 import rank_by_bm25
from fast_api.app.services.memory_system import MemoryManager


class _MemoryStub:
    def __init__(self, content, *, importance=0.5, recency_score=0.5):
        self.id = id(self)
        self.category = "risk"
        self.memory_type = "medical_context"
        self.summary = content
        self.content = content
        self.importance = importance
        self.recency_score = recency_score
        self.memory_metadata = {}


def test_memory_manager_maps_existing_memory_types_to_categories():
    manager = MemoryManager(db=None)

    assert manager._category_from_type("medical_context") == "risk"
    assert manager._category_from_type("nutrition_habit") == "nutrition"
    assert manager._category_from_type("training_performance") == "training"
    assert manager._category_from_type("stable_preference") == "preference"


def test_memory_manager_compacts_catalog_summaries():
    manager = MemoryManager(db=None)
    long_content = "训练记录 " * 80

    summary = manager._compact_summary(long_content, max_chars=60)

    assert len(summary) <= 60
    assert summary.endswith("...")


def test_memory_manager_bm25_score_can_prioritize_exact_memory_match_without_vector():
    manager = MemoryManager(db=None)
    exact = _MemoryStub("甲亢用户正在服用赛治，避免 HIIT 和极限心率训练。", importance=0.4)
    unrelated = _MemoryStub("用户偏好哑铃卧推和上肢训练。", importance=0.95)
    memories = [unrelated, exact]
    bm25_scores = {
        match.item.id: match.normalized_score
        for match in rank_by_bm25(memories, "甲亢 赛治 HIIT", manager._memory_bm25_document)
    }

    assert manager._memory_score(exact, bm25_scores[exact.id], {}) > manager._memory_score(
        unrelated,
        bm25_scores[unrelated.id],
        {},
    )
