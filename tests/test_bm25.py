from fast_api.app.services.bm25 import rank_by_bm25, tokenize_for_bm25


def test_bm25_tokenizer_handles_mixed_chinese_and_english_terms():
    tokens = tokenize_for_bm25("甲亢用户问 HIIT 和 BCAA")

    assert "甲亢" in tokens
    assert "hiit" in tokens
    assert "bcaa" in tokens


def test_bm25_ranks_exact_fitness_entity_match_first():
    items = [
        {"id": "general", "text": "训练后可以根据疲劳程度调整容量。"},
        {"id": "thyroid", "text": "甲亢用户服用赛治期间避免高强度 HIIT。"},
        {"id": "nutrition", "text": "外卖减脂优先选择高蛋白和蔬菜。"},
    ]

    matches = sorted(
        rank_by_bm25(items, "甲亢 赛治 HIIT", lambda item: item["text"]),
        key=lambda match: match.normalized_score,
        reverse=True,
    )

    assert matches[0].item["id"] == "thyroid"
    assert matches[0].normalized_score == 1.0
