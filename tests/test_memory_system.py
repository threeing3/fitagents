from fast_api.app.services.memory_system import MemoryManager


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
