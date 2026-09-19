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


def recalculate_budget(plan: TripPlan, *, estimate_missing: bool = False, budget_limit: int | None = None) -> None:
    """Recompute after repair; zero remains zero and absent prices are explicit."""
    from ..models.schemas import Budget
    attractions = [a for day in plan.days for a in day.attractions]
    meals = [m for day in plan.days for m in day.meals]
    hotels = [day.hotel for day in plan.days[:-1] if day.hotel]
    # Serialization fills numeric defaults; retain the original uncertainty on edits/reloads.
    unknown = list(plan.budget.unknown_items) if plan.budget else []
    if any("ticket_price" not in a.model_fields_set for a in attractions):
        unknown.append("attraction_prices")
    if any("estimated_cost" not in m.model_fields_set for m in meals):
        unknown.append("meal_prices")
    if len(hotels) < max(0, len(plan.days) - 1) or any("estimated_cost" not in h.model_fields_set for h in hotels):
        unknown.append("hotel_prices")
    unknown.append("transportation_prices")
    travelers = max(1, plan.traveler_count)
    rooms = max(1, plan.room_count)
    values = dict(total_attractions=sum(a.ticket_price for a in attractions) * travelers,
                  total_meals=sum(m.estimated_cost for m in meals) * travelers,
                  total_hotels=sum(h.estimated_cost for h in hotels) * rooms, total_transportation=0)
    assumptions = []
    if estimate_missing or (plan.budget and plan.budget.assumptions):
        missing_tickets = sum(a.ticket_price == 0 and a.price_source == "unknown" for a in attractions)
        values["total_attractions"] += 80 * missing_tickets * travelers
        if missing_tickets:
            assumptions.append(f"{missing_tickets}个景点缺少票价，暂按80元/人/景点预留；不代表实际售价或收费。")
            unknown.append("attraction_prices")
        missing_nights = max(0, len(plan.days) - 1) - sum(h.estimated_cost > 0 for h in hotels)
        if missing_nights:
            accommodation = plan.days[0].accommodation if plan.days else ""
            nightly = 600 if any(s in accommodation for s in ("豪华", "五星")) else 350 if "舒适" in accommodation else 250
            values["total_hotels"] += missing_nights * nightly * rooms
            assumptions.append(f"住宿按{len(plan.days) - 1}晚、{rooms}间房计算，缺少报价的{missing_nights}晚按{nightly}元/房晚预留。")
            unknown.append("hotel_prices")
        values["total_transportation"] = sum(0 if transport_to_route_type(d.transportation) == "walking" else
            100 * max(1, (travelers + 3) // 4) if transport_to_route_type(d.transportation) == "driving"
            else 30 * travelers for d in plan.days)
        assumptions.append("市内交通按步行0元、公共交通30元/人/天、自驾100元/车/天预留；不含往返目的地的大交通。")
        assumptions.append(f"门票与餐饮按{travelers}人、住宿按{rooms}间房计算，所有金额仅作预算预留，出行前确认价格。")
    total = sum(values.values())
    plan.budget = Budget(**values, total=total, estimated=True,
                         unknown_items=sorted(set(unknown)), assumptions=assumptions,
                         limit_total=budget_limit, within_limit=None if budget_limit is None else total <= budget_limit)


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
    data_gaps = ["opening_hours_unavailable"]
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
        # 餐饮只能来自可信 POI。上游无候选时宁可暴露数据缺口，也不能为了让
        # 质量门禁通过而编造餐厅；这不会破坏景点路线本身的可执行性。
        if missing_meals or any(not getattr(meal, "poi_id", "") for meal in day.meals):
            if "meal_pois_unavailable" not in data_gaps:
                data_gaps.append("meal_pois_unavailable")
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
        data_gaps=data_gaps,
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
