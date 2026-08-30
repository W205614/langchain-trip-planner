"""确定性行程质量控制。

LLM 只负责生成候选行程；本模块不调用任何外部服务，负责在返回前执行
可重复、可测试的完整性与可行性检查，避免把关键约束交给模型猜测。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable, Protocol

from ..models.schemas import Attraction, DayPlan, TripPlan


REQUIRED_MEALS = {"breakfast", "lunch", "dinner"}
MAX_VISIT_MINUTES_PER_DAY = 480
MAX_ROUTE_MINUTES_PER_DAY = 120


class RoutePlanner(Protocol):
    """最小路线接口，方便在离线测试中替换高德客户端。"""

    def plan_route_by_locations(self, left, right, route_type: str, city: str | None = None) -> dict: ...


@dataclass
class PlanQuality:
    score: int
    passed: bool
    warnings: list[str]
    days_checked: int
    attractions_checked: int
    duplicate_attractions_removed: int = 0
    estimated_intra_day_distance_km: float = 0.0
    route_checked: bool = False
    actual_route_distance_km: float = 0.0
    actual_route_minutes: int = 0
    repairs: list[dict[str, Any]] | None = None
    data_gaps: list[str] | None = None
    degraded_days: list[int] | None = None

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
        # 当前高德 POI 查询未提供足够稳定的营业/预约事实，不能假装已校验。
        data_gaps=["opening_hours_unavailable"],
        degraded_days=[
            day.day_index for day in plan.days
            if getattr(day, "generation_mode", "llm") == "fallback"
        ],
    )


def transport_to_route_type(transportation: str) -> str:
    """将前端中文交通偏好映射为高德路线类型。"""
    normalized = transportation.strip().lower()
    if any(word in normalized for word in ("自驾", "驾车", "driving")):
        return "driving"
    if any(word in normalized for word in ("公交", "地铁", "公共交通", "transit")):
        return "transit"
    return "walking"


def repair_plan_routes(plan: TripPlan, route_planner: RoutePlanner, transportation: str) -> dict[str, Any]:
    """用真实高德路线验证同日相邻景点，超限时确定性删除末尾景点。

    路线服务不可用时不虚构导航数据，保留已验证 POI，并显式返回直线距离降级标记。
    """
    route_type = transport_to_route_type(transportation)
    total_distance_m = 0.0
    total_minutes = 0
    repairs: list[dict[str, Any]] = []
    data_gaps: list[str] = ["opening_hours_unavailable"]
    route_available = True

    for day in plan.days:
        while len(day.attractions) >= 2:
            route_distance_m = 0.0
            route_seconds = 0
            try:
                for left, right in zip(day.attractions, day.attractions[1:]):
                    route = route_planner.plan_route_by_locations(
                        left.location, right.location, route_type=route_type, city=plan.city
                    )
                    if not route or route.get("duration") is None:
                        raise RuntimeError("AMap route response is empty")
                    route_distance_m += float(route.get("distance", 0))
                    route_seconds += int(route["duration"])
            except Exception:
                route_available = False
                if "route_duration_unavailable_fallback_to_straight_line" not in data_gaps:
                    data_gaps.append("route_duration_unavailable_fallback_to_straight_line")
                break

            route_minutes = (route_seconds + 59) // 60
            if route_minutes <= MAX_ROUTE_MINUTES_PER_DAY:
                total_distance_m += route_distance_m
                total_minutes += route_minutes
                break

            removed = day.attractions.pop()
            repairs.append({
                "day_index": day.day_index,
                "removed_poi_id": removed.poi_id,
                "reason": "route_duration_exceeded",
            })

    return {
        "route_checked": route_available,
        "actual_route_distance_km": round(total_distance_m / 1000, 2),
        "actual_route_minutes": total_minutes,
        "repairs": repairs,
        "data_gaps": data_gaps,
    }
