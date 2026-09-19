from app.services.assistant_intent import classify_assistant_intent


def test_multi_day_revision_requires_target_day():
    result = classify_assistant_intent("当天轻松一些", travel_days=3)

    assert result["operation"] == "propose_change"
    assert result["missing_slots"] == ["target_day"]
    assert result["confirmation_required"] is True


def test_single_day_revision_infers_only_day_and_extracts_patch():
    result = classify_assistant_intent("轻松一些，再增加火锅", travel_days=1)

    assert result["target_day"] == 0
    assert result["constraints_patch"]["pace"] == "relaxed"
    assert "meal_keyword" in result["constraints_patch"]


def test_hotel_recommendation_is_read_only_but_replacement_is_revision():
    recommendation = classify_assistant_intent("推荐附近酒店", travel_days=2)
    replacement = classify_assistant_intent("把第2天酒店换掉", travel_days=2)

    assert recommendation["intent"] == "recommend_hotel"
    assert recommendation["operation"] == "read_only"
    assert replacement["target_day"] == 1
    assert replacement["constraints_patch"]["replace_hotel"] is True


def test_out_of_range_day_is_clarified_instead_of_writing():
    result = classify_assistant_intent("把第4天调整轻松一些", travel_days=2)

    assert result["target_day"] is None
    assert result["missing_slots"] == ["target_day"]
