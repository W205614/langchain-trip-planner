"""确定性质量控制测试，不依赖 LLM/高德。"""

from app.models.schemas import Attraction, DayPlan, Location, Meal, TripPlan
from app.services.plan_quality import evaluate_plan, normalize_day


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
