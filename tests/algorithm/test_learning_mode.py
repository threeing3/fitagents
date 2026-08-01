import json

from algorithm.learning.curriculum import CURRICULUM, get_module
from algorithm.learning.mode import check_module
from algorithm.learning.progress import ProgressStore, default_state


def test_curriculum_has_unique_modules_and_interview_questions():
    module_ids = [card.module_id for card in CURRICULUM]
    assert len(module_ids) == 8
    assert len(set(module_ids)) == len(module_ids)
    assert all(card.exercises and card.questions and card.acceptance for card in CURRICULUM)
    assert get_module("03_intent_and_routing").track == "应用算法"


def test_progress_store_roundtrip(tmp_path):
    path = tmp_path / "learning_progress.json"
    store = ProgressStore(path)
    store.update("01_data_contracts", "in_progress", "完成 Schema round-trip", "补充异常字段测试")
    state = store.load()
    assert state["modules"]["01_data_contracts"]["status"] == "in_progress"
    assert "Schema" in state["modules"]["01_data_contracts"]["evidence"]
    json.loads(path.read_text(encoding="utf-8"))


def test_default_state_contains_all_curriculum_modules():
    state = default_state()
    assert set(state["modules"]) == {card.module_id for card in CURRICULUM}
    assert all(value["status"] == "not_started" for value in state["modules"].values())


def test_intent_learning_check_passes_with_current_baseline():
    result = check_module("03_intent_and_routing")
    assert result["passed"] is True
