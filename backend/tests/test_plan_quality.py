"""确定性质量控制测试，不依赖 LLM/高德。"""

from app.models.schemas import Attraction, DayPlan, Location, Meal, TripPlan
from app.services.plan_quality import evaluate_plan, normalize_day, repair_plan_routes


def _attraction(name: str, longitude: float, duration: int = 120) -> Attraction:
    return Attraction(
        name=name,
        poi_id=f"test-{name}",
        address="测试地址",
        location=Location(longitude=longitude, latitude=39.9),
        visit_duration=duration,
        description="测试景点",
    )


def test_normalize_day_removes_duplicates_and_overpacked_attractions():
    day = DayPlan(
        date="2026-08-01", day_index=0, description="测试", transportation="步行", accommodation="酒店",
        attractions=[
            _attraction("景点 A", 116.1, 240),
            _attraction("景点 A", 116.2, 120),
            _attraction("景点 B", 116.3, 300),
        ],
        meals=[],
    )
    removed = normalize_day(day)
    assert removed == 2
    assert [item.name for item in day.attractions] == ["景点 A"]


def test_evaluate_plan_reports_missing_meals():
    plan = TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-01",
        days=[DayPlan(
            date="2026-08-01", day_index=0, description="测试", transportation="步行", accommodation="酒店",
            attractions=[_attraction("景点 A", 116.1)],
            meals=[Meal(type="breakfast", name="早餐")],
        )],
        overall_suggestions="测试",
    )
    quality = evaluate_plan(plan, expected_days=1)
    assert quality.passed is False
    assert "dinner" in quality.warnings[0]
    assert quality.score < 100


class _RoutePlanner:
    def __init__(self, duration: int | None = 30, fails: bool = False):
        self.duration = duration
        self.fails = fails
        self.calls = 0

    def plan_route_by_locations(self, *_args, **_kwargs):
        self.calls += 1
        if self.fails:
            raise RuntimeError("AMap unavailable")
        return {"distance": 2_000, "duration": self.duration * 60}


def _three_attraction_plan() -> TripPlan:
    return TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-01",
        days=[DayPlan(
            date="2026-08-01", day_index=0, description="测试", transportation="步行", accommodation="酒店",
            attractions=[_attraction("A", 116.1), _attraction("B", 116.2), _attraction("C", 116.3)],
            meals=[Meal(type="breakfast", name="早"), Meal(type="lunch", name="午"), Meal(type="dinner", name="晚")],
        )],
        overall_suggestions="测试",
    )


def test_route_check_accepts_real_route_duration_under_limit():
    plan = _three_attraction_plan()

    quality = repair_plan_routes(plan, _RoutePlanner(duration=30), "步行")

    assert quality["route_checked"] is True
    assert quality["actual_route_minutes"] == 60
    assert quality["repairs"] == []


def test_route_check_removes_last_poi_until_duration_is_feasible():
    plan = _three_attraction_plan()

    quality = repair_plan_routes(plan, _RoutePlanner(duration=70), "步行")

    assert [item.poi_id for item in plan.days[0].attractions] == ["test-A", "test-B"]
    assert quality["actual_route_minutes"] == 70
    assert quality["repairs"] == [{
        "day_index": 0, "removed_poi_id": "test-C", "reason": "route_duration_exceeded"
    }]


def test_route_check_degrades_to_straight_line_when_route_api_fails():
    plan = _three_attraction_plan()

    quality = repair_plan_routes(plan, _RoutePlanner(fails=True), "公共交通")

    assert quality["route_checked"] is False
    assert quality["actual_route_minutes"] == 0
    assert "route_duration_unavailable_fallback_to_straight_line" in quality["data_gaps"]
    assert len(plan.days[0].attractions) == 3
