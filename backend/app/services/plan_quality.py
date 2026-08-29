"""确定性行程质量控制。

LLM 只负责生成候选行程；本模块不调用任何外部服务，负责在返回前执行
可重复、可测试的完整性与可行性检查，避免把关键约束交给模型猜测。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Iterable

from ..models.schemas import Attraction, DayPlan, TripPlan


REQUIRED_MEALS = {"breakfast", "lunch", "dinner"}
MAX_VISIT_MINUTES_PER_DAY = 480


@dataclass
class PlanQuality:
    score: int
    passed: bool
    warnings: list[str]
    days_checked: int
    attractions_checked: int
    duplicate_attractions_removed: int = 0
    estimated_intra_day_distance_km: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def haversine_km(left: Attraction, right: Attraction) -> float:
    """计算两景点直线距离，用于稳定地排序同日景点并暴露质量信号。"""
    lat1, lon1 = radians(left.location.latitude), radians(left.location.longitude)
    lat2, lon2 = radians(right.location.latitude), radians(right.location.longitude)
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def order_attractions_by_proximity(attractions: Iterable[Attraction]) -> list[Attraction]:
    """最近邻排序，减少明显的同日折返；真实导航仍由高德路线接口负责。"""
    remaining = list(attractions)
    if len(remaining) < 3:
        return remaining
    ordered = [remaining.pop(0)]
    while remaining:
        current = ordered[-1]
        next_index = min(range(len(remaining)), key=lambda idx: haversine_km(current, remaining[idx]))
        ordered.append(remaining.pop(next_index))
    return ordered


def normalize_day(day: DayPlan) -> int:
    """去重、限制每日游览时长并按距离排序，返回移除景点数。"""
    seen: set[str] = set()
    kept: list[Attraction] = []
    minutes = 0
    for attraction in day.attractions:
        key = attraction.name.strip().casefold()
        if not key or key in seen:
            continue
        duration = max(0, attraction.visit_duration)
        if kept and minutes + duration > MAX_VISIT_MINUTES_PER_DAY:
            continue
        seen.add(key)
        kept.append(attraction)
        minutes += duration
    removed = len(day.attractions) - len(kept)
    day.attractions = order_attractions_by_proximity(kept)
    return removed


def evaluate_plan(plan: TripPlan, expected_days: int) -> PlanQuality:
    """返回面向 API 与监控的质量报告，不将告警伪装成模型事实。"""
    warnings: list[str] = []
    if len(plan.days) != expected_days:
        warnings.append(f"行程天数为 {len(plan.days)}，与请求的 {expected_days} 天不一致")

    attraction_count = 0
    total_distance = 0.0
    for index, day in enumerate(plan.days, start=1):
        attraction_count += len(day.attractions)
        if not day.attractions:
            warnings.append(f"第 {index} 天没有可验证的景点")
        meal_types = {meal.type for meal in day.meals}
        missing_meals = REQUIRED_MEALS - meal_types
        if missing_meals:
            warnings.append(f"第 {index} 天缺少餐饮安排：{','.join(sorted(missing_meals))}")
        visit_minutes = sum(max(0, item.visit_duration) for item in day.attractions)
        if visit_minutes > MAX_VISIT_MINUTES_PER_DAY:
            warnings.append(f"第 {index} 天游览时长 {visit_minutes} 分钟，超过 {MAX_VISIT_MINUTES_PER_DAY} 分钟")
        total_distance += sum(
            haversine_km(day.attractions[idx], day.attractions[idx + 1])
            for idx in range(max(0, len(day.attractions) - 1))
        )

    score = max(0, 100 - 15 * len(warnings))
    return PlanQuality(
        score=score,
        passed=not warnings,
        warnings=warnings,
        days_checked=len(plan.days),
        attractions_checked=attraction_count,
        estimated_intra_day_distance_km=round(total_distance, 2),
    )
