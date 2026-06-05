from types import SimpleNamespace

from fast_api.app.services.agent_task_state import AgentTaskStateService


def test_training_issue_message_creates_experiment_signal():
    service = AgentTaskStateService(db=None)

    assert service._looks_like_training_experiment(
        "今天练胸，卧推55kg后后续动作没有力量，质量不好",
        "training_log",
    )


def test_goal_next_actions_include_weekly_review_for_fat_loss():
    service = AgentTaskStateService(db=None)
    profile = SimpleNamespace(goal="fat_loss")

    actions = service._next_actions_for_goal(profile)

    assert any(action["action"] == "weekly_review" for action in actions)
