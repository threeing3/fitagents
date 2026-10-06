import pytest

from fast_api.app.services.exercise_constraints import excluded_movement, exercise_exclusions


@pytest.mark.parametrize("text", ["别安排推举", "不要做肩推", "avoid overhead press"])
def test_explicit_exclusion(text):
    assert exercise_exclusions(text) == ["overhead_press"]


@pytest.mark.parametrize(
    "text", ["他说‘别安排推举’", "如果不要做推举会怎样", "是否避免推举", "不要不安排推举"]
)
def test_noncommands(text):
    assert exercise_exclusions(text) == []


@pytest.mark.parametrize("name", ["哑铃推举", "坐姿肩推", "Overhead Press", "vertical push"])
def test_aliases(name):
    assert excluded_movement(name, ["overhead_press"])


def test_exclusion_never_removes_record_refusal():
    from fast_api.app.services.chat_workout_record import parse_workout_record

    result = parse_workout_record("别安排推举，也别帮我记录。我刚完成30分钟跑步。")
    assert result["status"] == "blocked"


@pytest.mark.parametrize(
    "text",
    [
        "他说“先热身，别安排推举，再休息”。",
        "她说‘先跑步；不要做肩推；再整理’。",
        '示例："热身,avoid overhead press,rest"。',
        "```\n先热身\n不要做推举\n再休息\n```",
        "`先热身，别安排推举，再休息`",
        "他说“先热身，别安排推举，后文没有闭合引号",
        "举例：别安排推举",
    ],
)
def test_quoted_separators_never_create_exclusion(text):
    assert exercise_exclusions(text) == []


def test_real_exclusion_outside_quotation_is_preserved():
    assert exercise_exclusions("他说“不要做肩推”；但我要求别安排推举。") == ["overhead_press"]
