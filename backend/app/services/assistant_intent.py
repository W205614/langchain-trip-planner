"""Bounded intent and slot extraction for itinerary conversations.

This parser deliberately covers a small, auditable command set. Unknown or
ambiguous utterances remain research questions instead of causing writes.
"""
from __future__ import annotations

import re

_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _day_index(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]{1,2}|[一二三四五六七八九十])\s*天", text)
    if not match:
        return None
    raw = match.group(1)
    value = int(raw) if raw.isdigit() else _CN.get(raw)
    return value - 1 if value and value > 0 else None


def classify_assistant_intent(content: str, travel_days: int) -> dict:
    text = re.sub(r"\s+", "", content or "")
    day_index = _day_index(text)
    hotel_change = (
        ("酒店" in text and any(word in text for word in ("更换", "换", "替换", "改")))
        or ("住宿" in text and any(word in text for word in ("更换", "换", "替换", "改")))
    )
    hotel_read = "酒店" in text and any(word in text for word in ("推荐", "附近", "哪家", "怎么样")) and not hotel_change
    revision = hotel_change or any(word in text for word in (
        "轻松", "少走", "增加", "加入", "删除", "去掉", "替换", "改排", "调整", "换成", "不要去",
    ))

    if hotel_read:
        return {"intent": "recommend_hotel", "operation": "read_only", "target_day": day_index,
                "missing_slots": [], "constraints_patch": {}, "confirmation_required": False,
                "reply": "我会基于当前行程和公开资料推荐附近酒店，不会直接修改住宿。"}
    if not revision:
        return {"intent": "research", "operation": "read_only", "target_day": day_index,
                "missing_slots": [], "constraints_patch": {}, "confirmation_required": False, "reply": ""}

    if day_index is None and travel_days == 1:
        day_index = 0
    if day_index is None:
        return {"intent": "revise_day", "operation": "propose_change", "target_day": None,
                "missing_slots": ["target_day"], "constraints_patch": {}, "confirmation_required": True,
                "reply": "你想调整第几天？请说“第1天”“第2天”等，我会只生成待确认方案。"}
    if day_index >= travel_days:
        return {"intent": "revise_day", "operation": "propose_change", "target_day": None,
                "missing_slots": ["target_day"], "constraints_patch": {}, "confirmation_required": True,
                "reply": f"当前行程只有{travel_days}天，请重新指定要调整的日期。"}

    patch: dict[str, object] = {}
    if any(word in text for word in ("轻松", "少走", "别太累")):
        patch["pace"] = "relaxed"
    meal = re.search(r"(?:增加|加入|安排|想吃)([^，。！？]{1,16}?)(?:店|餐|$)", text)
    if meal and any(word in text for word in ("吃", "火锅", "餐", "美食", "小吃")):
        patch["meal_keyword"] = meal.group(1)
    if hotel_change:
        patch["replace_hotel"] = True
    return {"intent": "revise_day", "operation": "propose_change", "target_day": day_index,
            "missing_slots": [], "constraints_patch": patch, "confirmation_required": True,
            "reply": f"已识别为第{day_index + 1}天的局部调整，将生成待确认方案。"}
