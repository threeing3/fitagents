"""
Feedback learner integration into coach_agent.py.

This patches the _coaching_reply method to use adaptive prompts
that incorporate learned user preferences from their feedback history.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_adaptive_system_prompt(
    db,
    user_id,
    prompt_id: str,
    prompt_registry,
) -> tuple[str, dict[str, Any]]:
    """Get a system prompt enhanced with user feedback patterns.

    This is the integration point called from CoachAgentService.
    It fetches the base prompt from the registry, then layers on
    user-specific behavioral guidance learned from their ratings.

    Returns (enhanced_prompt, debug_info_dict).
    """
    from fast_api.app.services.feedback_learner import (
        FeedbackCollector,
        PreferenceLearner,
        PromptEnhancer,
    )

    base_prompt = prompt_registry.get(prompt_id)

    try:
        collector = FeedbackCollector(db)
        learner = PreferenceLearner(collector)
        enhancer = PromptEnhancer(learner)
        enhanced, debug = enhancer.enhance_system_prompt(user_id, base_prompt)
        return enhanced, debug
    except Exception as exc:
        logger.warning(
            "Failed to enhance prompt with feedback patterns for user=%s: %s",
            user_id, exc,
        )
        return base_prompt, {"enhanced": False, "error": str(exc)}
