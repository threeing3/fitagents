"""Bounded current-turn exclusions; quoted text never creates constraints."""

import re

PRESS_ALIASES = ("推举", "肩推", "shoulder press", "overhead press", "vertical push")
EXCLUSION_REPLY = "本轮建议包含你明确排除的动作，已停止展示。已保存的训练记录不受影响；请确认其他允许的动作后再安排。"


def exercise_exclusions(message: str) -> list[str]:
    # Preserve separators while removing all quoted/code content before splitting.
    # Unclosed quoting is conservative: its remaining text is not a command.
    visible = []
    closer = None
    index = 0
    pairs = {"“": "”", "‘": "’", '"': '"', "'": "'", "`": "`"}
    while index < len(message):
        if message.startswith("```", index):
            if closer is None:
                closer = "```"
            elif closer == "```":
                closer = None
            visible.append(" ")
            index += 3
            continue
        character = message[index]
        if closer is not None:
            if character == closer:
                closer = None
            visible.append(" ")
        elif character in pairs:
            closer = pairs[character]
            visible.append(" ")
        else:
            visible.append(character)
        index += 1
    message = "".join(visible)
    for clause in re.split(r"[；;。！？!?，,\n]", message):
        if re.search(r"如果|假如|他说|她说|是否|能否|吗|不要不|不是不|举例|例如|示例|转述", clause):
            continue
        if re.search(
            r"(?:禁止|别(?:做|安排)?|不要(?:做|安排)?|不做|不安排|避免|排除|跳过).{0,8}(?:推举|肩推)",
            clause,
        ) or re.search(
            r"(?:avoid|skip|do not).{0,12}(?:shoulder press|overhead press)", clause, re.I
        ):
            return ["overhead_press"]
    return []


def excluded_movement(name: str, exclusions: list[str]) -> bool:
    return "overhead_press" in exclusions and any(alias in name.lower() for alias in PRESS_ALIASES)


def violates_exclusions(response: str, exclusions: list[str]) -> bool:
    # Fail closed even for acknowledgments mentioning the movement: the
    # replacement says "excluded movement" without trying to classify advice.
    return excluded_movement(response, exclusions)
