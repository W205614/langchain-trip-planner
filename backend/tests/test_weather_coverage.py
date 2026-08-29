"""天气预报覆盖范围测试：高德只提供近期预报，不能伪装成完整行程天气。"""

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import TripRequest, WeatherInfo


def _request(start_date: str, end_date: str, days: int) -> TripRequest:
    return TripRequest(
        city="北京", start_date=start_date, end_date=end_date, travel_days=days,
        transportation="公共交通", accommodation="经济型酒店",
    )


def test_weather_is_limited_to_trip_dates_and_reports_uncovered_days():
    forecast = [
        WeatherInfo(date=f"2026-08-0{day}", day_weather="晴", day_temp=30)
        for day in range(1, 5)
    ]
    relevant, notice = MultiAgentTripPlanner._filter_weather_for_trip(
        forecast, _request("2026-08-03", "2026-08-07", 5)
    )
    assert [item.date for item in relevant] == ["2026-08-03", "2026-08-04"]
    assert "3 天" in notice


def test_weather_has_no_notice_when_all_trip_dates_are_covered():
    forecast = [
        WeatherInfo(date=f"2026-08-0{day}", day_weather="晴", day_temp=30)
        for day in range(1, 5)
    ]
    relevant, notice = MultiAgentTripPlanner._filter_weather_for_trip(
        forecast, _request("2026-08-02", "2026-08-04", 3)
    )
    assert len(relevant) == 3
    assert notice == ""
