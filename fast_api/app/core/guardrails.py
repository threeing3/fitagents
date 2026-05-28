"""
Safety guardrails for LLM-generated fitness coach responses.

Three severity tiers:
- BLOCK  — Replace the response entirely (clear and present danger)
- WARN   — Flag but deliver (potentially risky)
- PASS   — No action needed

Reference: Anthropic safety taxonomy, fitness/medical safety best practices.
"""

import logging
import re

from fast_api.app.core.prompts import registry
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    BLOCK = "block"   # Response must be replaced
    WARN = "warn"     # Response delivered with a safety flag
    PASS = "pass"     # No safety concern


@dataclass
class Flag:
    """A single safety flag raised by the guardrail."""
    rule_id: str
    severity: Severity
    category: str        # e.g. "medical_boundary", "dangerous_advice", "missing_disclaimer"
    message: str         # Human-readable reason
    matched_text: str | None = None  # The substring that triggered the flag


@dataclass
class GuardrailResult:
    """Result of running guardrails on a response."""
    action: Severity                              # Overall verdict
    flags: list[Flag] = field(default_factory=list)
    blocked_replacement: str | None = None        # Replacement text if BLOCK
    passed: bool = True                           # Convenience — True if not BLOCK


# ---- Block-level replacement messages ----

def _get_block_replacement_medical() -> str:
    return registry.get("guardrail_block_medical")

BLOCK_REPLACEMENT_MEDICAL = _get_block_replacement_medical()  # kept for backwards compat

def _get_block_replacement_dangerous() -> str:
    return registry.get("guardrail_block_dangerous")

BLOCK_REPLACEMENT_DANGEROUS = _get_block_replacement_dangerous()  # kept for backwards compat

def _get_block_replacement_generic() -> str:
    return registry.get("guardrail_block_generic")

BLOCK_REPLACEMENT_GENERIC = _get_block_replacement_generic()  # kept for backwards compat

# ---- Rule definitions ----

RuleFunc = Callable[[str, dict[str, Any] | None], list[Flag]]


def _compile_rules() -> list[RuleFunc]:
    """Build the list of all active guardrail rules."""
    rules: list[RuleFunc] = []

    # ---------------------------------------------------------------
    # BLOCK rules — must never reach the user
    # ---------------------------------------------------------------

    def _medication_discontinuation(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect advice to stop or change prescribed medication."""
        patterns = [
            (r"(?i)stop\s+taking?\s+(?:your\s+)?(?:medication|medicine|prescription|pills?|drugs?)", "stop_medication"),
            (r"(?i)don'?t\s+(?:need\s+)?(?:to\s+)?take\s+(?:your\s+)?(?:medication|medicine|pills?)", "dont_take_medication"),
            (r"(?i)(?:you\s+)?should(?:n'?t)?\s+(?:be\s+)?(?:taking|using)\s+(?:your\s+)?(?:medication|medicine)", "shouldnt_take_medication"),
            (r"(?i)(?:reduce|cut|halve)\s+(?:your\s+)?(?:medication|medicine|dose|dosage)", "reduce_medication"),
            (r"(?i)wean\s+(?:yourself\s+)?off\s+(?:your\s+)?(?:medication|medicine)", "wean_off_medication"),
            (r"(?i)replace\s+(?:your\s+)?(?:medication|medicine)\s+with", "replace_medication"),
            (r"(?i)(?:停|不[用要]|不要[用吃]|戒掉|减少|减量)\s*(?:你[的]?)?(?:药|药物|药品|处方药)", "stop_medication_cn"),
        ]
        flags: list[Flag] = []
        for pattern, rule_id in patterns:
            match = re.search(pattern, response)
            if match:
                flags.append(Flag(
                    rule_id=rule_id,
                    severity=Severity.BLOCK,
                    category="medical_boundary",
                    message="Advice to stop or change prescribed medication",
                    matched_text=match.group(0),
                ))
        return flags

    def _medical_diagnosis(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect attempts to give medical diagnoses."""
        patterns = [
            (r"(?i)(?:you\s+(?:may\s+)?have|you'?re\s+suffering\s+from|this\s+is\s+definitely|you'?ve\s+got)\s+(?:a\s+)?(?:condition|disease|disorder|illness|syndrome|cancer|tumou?r|thyroid\s+disease|diabetes|heart\s+disease)", "medical_diagnosis"),
            (r"(?i)(?:diagnos|diagnosable)\s+(?:you\s+)?(?:with|as)", "diagnose_with"),
            (r"(?i)(?:你|您)(?:可能)?[得有患了]\s*(?:什么|某[种些]|一[种些])?\s*(?:病|疾病|病症|癌症|肿瘤|心脏病|糖尿病|甲亢|甲减|桥本)", "medical_diagnosis_cn"),
            (r"(?i)(?:this\s+is\s+a\s+clear\s+sign\s+of|this\s+means\s+you\s+have)", "clear_sign_of"),
            (r"(?i)(?:你的|您的).*(?:症状|情况).*(?:就是|肯定是|绝对是|一定是)", "diagnosis_cn2"),
        ]
        flags: list[Flag] = []
        for pattern, rule_id in patterns:
            match = re.search(pattern, response)
            if match:
                # Only flag if context doesn't already include a safety framing
                # (e.g., "you should see a doctor if you have" is fine)
                surrounding = response[max(0, match.start()-40):match.end()+40]
                if re.search(r"(?i)(?:see|consult|visit|talk\s+to)\s+(?:a\s+)?(?:doctor|physician|specialist|GP)", surrounding):
                    continue
                flags.append(Flag(
                    rule_id=rule_id,
                    severity=Severity.BLOCK,
                    category="medical_boundary",
                    message="Potential medical diagnosis by AI coach",
                    matched_text=match.group(0),
                ))
        return flags

    def _dangerous_calorie_restriction(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect dangerously low calorie recommendations."""
        patterns = [
            (r"(?i)(?:eat|consume|have|only)\s+(?:under\s+)?(\d{2,4})\s*(?:-\s*\d+)?\s*(?:calories?|kcal|cal)(?:\s*(?:a\s+)?day)?", "low_calorie"),
            (r"(?i)(?:每天|每日|一天)[只就]?\s*(?:吃|摄入)\s*(\d{2,4})\s*(?:卡|大卡|千卡|卡路里)", "low_calorie_cn"),
            (r"(?i)(?:limit|restrict)\s+(?:yourself|calories?)\s+to\s+(\d{2,4})\s*(?:calories?|kcal)", "restrict_calories"),
            (r"(?i)fast\s+(?:for|more\s+than)\s+\d+\s+(?:days?|hours?)", "extended_fast"),
        ]
        flags: list[Flag] = []
        for pattern, rule_id in patterns:
            match = re.search(pattern, response)
            if match:
                # Extract the calorie number if present
                calorie_num = None
                groups = match.groups()
                if groups and groups[0]:
                    try:
                        calorie_num = int(groups[0])
                    except ValueError:
                        pass
                # Flag if calories are dangerously low (< 800) or if the number is
                # embedded in a harmful context
                if calorie_num is not None and calorie_num < 800:
                    flags.append(Flag(
                        rule_id=rule_id,
                        severity=Severity.BLOCK,
                        category="dangerous_advice",
                        message=f"Dangerously low calorie recommendation ({calorie_num} kcal)",
                        matched_text=match.group(0),
                    ))
                elif calorie_num is None:
                    # Pattern matched but no number extracted — still suspicious
                    flags.append(Flag(
                        rule_id=rule_id,
                        severity=Severity.WARN,
                        category="dangerous_advice",
                        message="Potentially dangerous calorie restriction advice",
                        matched_text=match.group(0),
                    ))
        return flags

    def _train_through_pain(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect advice to ignore pain and continue training."""
        patterns = [
            (r"(?i)(?:train|work\s*out|exercise|push|go)\s+(?:through|despite|regardless\s+of)\s+(?:the\s+)?(?:pain|injury|hurt)", "train_through_pain"),
            (r"(?i)(?:ignore|disregard|don'?t\s+worry\s+about)\s+(?:the\s+)?(?:pain|sharp\s+pain|injury)", "ignore_pain"),
            (r"(?i)(?:pain|injury)\s+(?:is|it'?s)\s+(?:just|only|nothing|fine|normal|ok)", "pain_is_fine"),
            (r"(?i)(?:no\s+pain\s+no\s+gain|pain\s+is\s+weakness\s+leaving)", "no_pain_no_gain"),
            (r"(?i)(?:别[管怕]|不[要怕用]|忍[着住]|[坚忍]持[一一下]|继续).*(?:疼|痛|伤)", "push_through_pain_cn"),
            (r"(?i)(?:疼[痛]|伤).*(?:没[事关]|不[要紧]|正[常的])", "pain_normal_cn"),
        ]
        flags: list[Flag] = []
        for pattern, rule_id in patterns:
            match = re.search(pattern, response)
            if match:
                flags.append(Flag(
                    rule_id=rule_id,
                    severity=Severity.BLOCK,
                    category="dangerous_advice",
                    message="Advice to train through pain or ignore injury",
                    matched_text=match.group(0),
                ))
        return flags

    def _skip_warmup(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect advice to skip warmup."""
        patterns = [
            (r"(?i)(?:no\s+need|don'?t\s+(?:need|have|bother)|skip|unnecessary)\s+(?:to\s+)?(?:warm\s*up|warmup)", "skip_warmup"),
            (r"(?i)warm(?:-|\s*)up(?:s)?\s+(?:is|are|it'?s)\s+(?:unnecessary|a\s+waste|useless|pointless|overrated)", "warmup_unnecessary"),
        ]
        return [
            Flag(rule_id=rule_id, severity=Severity.BLOCK, category="dangerous_advice",
                 message="Advice to skip or minimize warmup", matched_text=m.group(0))
            for pattern, rule_id in patterns
            for m in [re.search(pattern, response)] if m
        ]

    def _dangerous_supplements(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Detect advice promoting dangerous or illegal supplements."""
        patterns = [
            (r"(?i)(?:take|use|try|recommend|suggest)\s+(?:steroids?|anabolics?|SARMs|clenbuterol|DNP|trenbolone|dianabol)", "dangerous_supplement"),
            (r"(?i)(?:减肥药|类固醇|瘦肉精)", "dangerous_supplement_cn"),
            (r"(?i)(?:snort|inject\s+(?:yourself\s+)?with|intravenous)", "dangerous_administration"),
        ]
        return [
            Flag(rule_id=rule_id, severity=Severity.BLOCK, category="dangerous_advice",
                 message="Recommendation of dangerous substance", matched_text=m.group(0))
            for pattern, rule_id in patterns
            for m in [re.search(pattern, response)] if m
        ]

    # ----------------------------------------------------------------
    # WARN rules — flag but deliver
    # ----------------------------------------------------------------

    def _missing_disclaimer(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Flag responses that discuss medical topics without a disclaimer."""
        medical_topics = [
            r"(?i)(?:injury|pain|hurt|sore\s+(?:joint|knee|back|shoulder)|sprain|strain|fracture)",
            r"(?i)(?:disease|condition|disorder|syndrome|chronic|diagnosis)",
            r"(?i)(?:thyroid|diabetes|heart|blood\s+pressure|cholesterol)",
            r"(?i)(?:medication|medicine|prescription|drug)",
            r"(?i)(?:pregnancy|pregnant|breastfeed)",
            r"(?i)(?:surgery|operation|recovery\s+from)",
            r"(?i)(?:[伤损]|疼[痛]|关节|膝盖|腰[背椎]|骨[折]|扭[伤]|拉[伤])",
            r"(?i)(?:病|症|药|手术|怀[孕]|哺乳)",
        ]
        has_medical_topic = any(re.search(t, response) for t in medical_topics)
        has_disclaimer = bool(re.search(
            r"(?i)(?:consult|see|talk\s+to|speak\s+with|请咨询|建议咨询|就医|看医生).*(?:doctor|physician|medical|healthcare|professional|specialist|医生|医师|专家)",
            response,
        ))
        if has_medical_topic and not has_disclaimer:
            return [Flag(
                rule_id="missing_medical_disclaimer",
                severity=Severity.WARN,
                category="missing_disclaimer",
                message="Medical topic discussed without disclaimer or referral to doctor",
            )]
        return []

    def _missing_modifiers(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Flag absolute claims that should be qualified."""
        absolute_patterns = [
            (r"(?i)(?:always|never|must|have\s+to|guaranteed|certainly|definitely)\s+(?:do|eat|take|lift|train|work\s*out)", "absolute_advice"),
            (r"(?i)(?:一定|必须|绝对|肯定|保证).*(?:要|做|吃|练|训练)", "absolute_advice_cn"),
        ]
        return [
            Flag(rule_id=rule_id, severity=Severity.WARN, category="missing_modifier",
                 message="Absolute or unqualified fitness claim", matched_text=m.group(0))
            for pattern, rule_id in absolute_patterns
            for m in [re.search(pattern, response)] if m
        ]

    def _eating_disorder_triggers(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Flag language that could trigger eating disorders."""
        patterns = [
            (r"(?i)(?:purge|binge|starve\s+yourself|skip\s+all\s+meals|only\s+drink\s+water)",
             "ed_trigger_purge"),
            (r"(?i)(?:you'?re\s+(?:too\s+)?fat|you\s+need\s+to\s+lose\s+weight\s+fast)",
             "ed_trigger_body_shame"),
            (r"(?i)(?:cleanse|detox|flush\s+out\s+toxins)",
             "ed_trigger_detox"),
        ]
        return [
            Flag(rule_id=rule_id, severity=Severity.WARN, category="eating_disorder",
                 message="Language that may trigger eating disorders", matched_text=m.group(0))
            for pattern, rule_id in patterns
            for m in [re.search(pattern, response)] if m
        ]

    def _excessive_exercise(response: str, _ctx: dict[str, Any] | None) -> list[Flag]:
        """Flag recommendations for dangerous training volumes."""
        patterns = [
            (r"(?i)(?:train|work\s*out|exercise)\s+(?:every\s+single\s+day|7\s+days?\s+a\s+week|twice\s+a\s+day\s+every\s+day)", "excessive_frequency"),
            (r"(?i)(?:3\+?\s*hours?\s+(?:per|a)\s+(?:day|session|workout))", "excessive_duration"),
            (r"(?i)(?:每天[都]?训练|一周[七7]天|一天[两2]练|每天[两2]次)", "excessive_frequency_cn"),
        ]
        return [
            Flag(rule_id=rule_id, severity=Severity.WARN, category="excessive_exercise",
                 message="Recommendation of excessive training volume", matched_text=m.group(0))
            for pattern, rule_id in patterns
            for m in [re.search(pattern, response)] if m
        ]

    # Register all rules
    rules.extend([
        _medication_discontinuation,
        _medical_diagnosis,
        _dangerous_calorie_restriction,
        _train_through_pain,
        _skip_warmup,
        _dangerous_supplements,
        _missing_disclaimer,
        _missing_modifiers,
        _eating_disorder_triggers,
        _excessive_exercise,
    ])
    return rules


# ---- Main guardrail function ----

# Build once at module load
_GUARDRAIL_RULES: list[RuleFunc] = _compile_rules()


def run_guardrails(
    response: str,
    user_message: str | None = None,
    profile: Any | None = None,
    rules: list[RuleFunc] | None = None,
) -> GuardrailResult:
    """Run all guardrail rules against a coach response.

    Args:
        response: The coach's generated response text.
        user_message: The user's original message (for context).
        profile: The user's fitness profile (for context-aware checks).
        rules: Optional custom rule set (defaults to all built-in rules).

    Returns:
        GuardrailResult with action and flags.
    """
    if rules is None:
        rules = _GUARDRAIL_RULES

    context: dict[str, Any] = {}
    if user_message:
        context["user_message"] = user_message
    if profile:
        context["profile"] = profile

    all_flags: list[Flag] = []
    for rule_func in rules:
        try:
            rule_flags = rule_func(response, context)
            all_flags.extend(rule_flags)
        except Exception as exc:
            logger.warning("Guardrail rule %s raised: %s", rule_func.__name__, exc)

    # Determine overall action
    has_block = any(f.severity == Severity.BLOCK for f in all_flags)
    has_warn = any(f.severity == Severity.WARN for f in all_flags)

    if has_block:
        # Choose the most appropriate replacement message
        block_categories = {f.category for f in all_flags if f.severity == Severity.BLOCK}
        if "medical_boundary" in block_categories:
            replacement = BLOCK_REPLACEMENT_MEDICAL
        elif "dangerous_advice" in block_categories:
            replacement = BLOCK_REPLACEMENT_DANGEROUS
        else:
            replacement = BLOCK_REPLACEMENT_GENERIC

        return GuardrailResult(
            action=Severity.BLOCK,
            flags=all_flags,
            blocked_replacement=replacement,
            passed=False,
        )
    elif has_warn:
        return GuardrailResult(
            action=Severity.WARN,
            flags=all_flags,
            passed=True,
        )
    else:
        return GuardrailResult(
            action=Severity.PASS,
            flags=all_flags,
            passed=True,
        )


def quick_check(response: str) -> bool:
    """Quick safety check — returns True if response passes all BLOCK rules."""
    result = run_guardrails(response)
    return result.passed
