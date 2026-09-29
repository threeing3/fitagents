from algorithm.evaluation.intent_semantic_probe_tfidf import intent_checks


def test_intent_checks_use_the_same_required_and_forbidden_contract():
    row = {
        "required_intents": ["training_log", "memory_query"],
        "forbidden_intents": ["training_plan"],
    }
    prediction = {
        "primary_intent": "training_log",
        "secondary_intents": ["memory_query", "training_plan"],
    }

    assert intent_checks(row, prediction) == {
        "required_intents": True,
        "forbidden_intents": False,
    }
