"""Pure planner fixtures; no Python business database or public API."""
from app.models.schemas import TripPlan,DayPlan,Attraction,Meal,Location,Budget,WeatherInfo,TripRequest

def make_fake_trip_plan() -> TripPlan:
    """构造一个合法的旅行计划 (测试用固定数据)"""
    return TripPlan(
        city="北京",
        start_date="2026-08-01",
        end_date="2026-08-02",
        days=[
            DayPlan(
                date="2026-08-01",
                day_index=0,
                description="游览故宫",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[
                    Attraction(
                        name="故宫博物院",
                        poi_id="B000A8UIN9",
                        address="北京市东城区景山前街4号",
                        location=Location(longitude=116.397026, latitude=39.918058),
                        visit_duration=180,
                        description="明清皇宫",
                    )
                ],
                meals=[],
            ),
            DayPlan(
                date="2026-08-02",
                day_index=1,
                description="游览天安门",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
                meals=[],
            ),
        ],
        weather_info=[WeatherInfo(date="2026-08-01", day_weather="晴", day_temp=32)],
        overall_suggestions="提前预约门票",
        budget=Budget(total_attractions=100, total_meals=300, total=400),
    )

def make_complete_trip_plan():
    plan = make_fake_trip_plan()
    plan.days[1].attractions = [plan.days[0].attractions[0].model_copy(update={"poi_id": "second-poi", "name": "天安门广场"})]
    for day in plan.days:
        day.meals = [Meal(type=t, name=t) for t in ("breakfast", "lunch", "dinner")]
    return plan

VALID_REQUEST = {
    "city": "北京",
    "start_date": "2026-08-01",
    "end_date": "2026-08-02",
    "travel_days": 2,
    "transportation": "公共交通",
    "accommodation": "经济型酒店",
    "preferences": ["历史文化"],
    "free_text_input": "",
}
