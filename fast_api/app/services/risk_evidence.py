"""Conservative symptom attribution shared by routing, protocol and audit evidence."""

import re

SUBJECTS = re.compile(
    r"我的朋友|我的家人|我的同事|我的室友|朋友|家人|同事|室友"
    r"|妹妹|弟弟|姐姐|哥哥|妈妈|爸爸|母亲|父亲|(?<!其)他|她|本人|我"
    r"|\b(?:my friend|my colleague|my roommate|he|she|i)\b",
    re.I,
)
SELF = {"我", "本人", "i"}
ACUTE_SAFETY_TERMS = (
    "呼吸困难",
    "喘不上气",
    "胸口发紧",
    "胸口闷",
    "胸闷",
    "胸痛",
    "difficulty breathing",
    "shortness of breath",
    "chest tightness",
    "chest pain",
)


def self_risk_evidence(
    message: str, terms: list[str], context_terms: list[str] | None = None
) -> list[str]:
    """Keep unknown attribution conservative; suppress only explicit other/absent spans."""
    text = message.lower()
    ordered = sorted(set(terms), key=len, reverse=True)
    if not ordered:
        return []
    term_pattern = "|".join(
        rf"\b{re.escape(term)}\b" if term.isascii() and term.isalpha() else re.escape(term)
        for term in ordered
    )
    context_pattern = "|".join(
        rf"\b{re.escape(term)}\b" if term.isascii() and term.isalpha() else re.escape(term)
        for term in sorted(set(terms + (context_terms or [])), key=len, reverse=True)
    )
    # Negation covers a coordinated list, never a following contrasting clause.
    absent = re.compile(
        rf"(?:没有|没|并不|不是|无|未|不|\bno\b|\bnot\b|\bwithout\b)\s*(?:{context_pattern})"
        rf"(?:\s*(?:和|及|、|或|也没有|\band\b|\bor\b)\s*(?:{context_pattern}))*",
        re.I,
    )
    found = []
    subject = "unknown"
    for clause in re.split(r"([，,；;。！？!?\n]|但是|可是|不过|然而|但)", text):
        if clause in {"。", "！", "？", "!", "?", ";", "；", "\n"}:
            subject = "unknown"
            continue
        masked = list(clause)
        for match in absent.finditer(clause):
            prefix = clause[max(0, match.start() - 12) : match.start()]
            # A double negative or uncertainty is not positive evidence of absence.
            if re.search(r"不是|并非|不能说|不确定|不知道|不能排除|好像|似乎", prefix):
                continue
            masked[match.start() : match.end()] = " " * (match.end() - match.start())
        visible = "".join(masked)
        mentions = list(SUBJECTS.finditer(clause))
        for symptom in re.finditer(term_pattern, visible, re.I):
            preceding = [item for item in mentions if item.start() < symptom.start()]
            owner = preceding[-1].group().lower() if preceding else subject
            if owner == "unknown" or owner in SELF:
                if symptom.group() not in found:
                    found.append(symptom.group())
        if mentions:
            subject = mentions[-1].group().lower()
    return found


def acute_safety_signal(message: str) -> bool:
    """Escalate current red flags without treating quoted lyrics as symptoms."""

    for clause in re.split(r"[，,；;。！？!?\n]", message.lower()):
        if not clause.strip():
            continue
        quoted_context = any(word in clause for word in ("引用", "歌词", "引号"))
        if quoted_context and not any(word in clause for word in ("不是引用", "并非引用")):
            continue
        if self_risk_evidence(clause, list(ACUTE_SAFETY_TERMS)):
            return True
    return False
