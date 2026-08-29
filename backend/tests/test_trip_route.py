"""旅行规划路由测试: mock Agent, 不调用真实 LLM/高德

trip/plan 需登录 (JWT), 测试通过覆盖 get_current_user 依赖注入一个假用户。
"""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import get_current_user
from app.db.models import User
from app.models.schemas import (
    TripPlan,
    DayPlan,
    Attraction,
    Meal,
    Location,
    Budget,
    WeatherInfo,
)


@pytest.fixture(autouse=True)
def _override_auth(monkeypatch):
    """覆盖鉴权依赖: 所有请求视为已登录用户, 返回假 User"""
    from app.db import database as dbmod

    fake_user = User(id=1, username="tester", hashed_password="x")

    def _fake_get_current_user():
        return fake_user

    app.dependency_overrides[get_current_user] = _fake_get_current_user
    yield
    app.dependency_overrides.clear()


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


def test_plan_trip_success(client, monkeypatch):
    """正常生成旅行计划"""
    fake_agent = Mock()
    fake_agent.plan_trip.return_value = make_fake_trip_plan()
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent", lambda: fake_agent
    )

    resp = client.post("/api/trip/plan", json=VALID_REQUEST)

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["city"] == "北京"
    assert len(data["data"]["days"]) == 2
    assert data["data"]["budget"]["total"] == 400
    assert data["quality"]["days_checked"] == 2


def test_plan_trip_idempotency_reuses_the_first_result(client, monkeypatch):
    fake_agent = Mock()
    fake_agent.plan_trip.return_value = make_fake_trip_plan()
    monkeypatch.setattr("app.api.routes.trip.get_trip_planner_agent", lambda: fake_agent)

    headers = {"Idempotency-Key": "test-trip-route-repeat-key"}
    first = client.post("/api/trip/plan", json=VALID_REQUEST, headers=headers)
    second = client.post("/api/trip/plan", json=VALID_REQUEST, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert fake_agent.plan_trip.call_count == 1


def test_plan_stream_returns_complete_event(client, monkeypatch):
    fake_agent = Mock()
    fake_agent.plan_trip.return_value = make_fake_trip_plan()
    monkeypatch.setattr("app.api.routes.trip.get_trip_planner_agent", lambda: fake_agent)

    with client.stream("POST", "/api/trip/plan/stream", json=VALID_REQUEST) as response:
        body = "\n".join(response.iter_lines())
    assert response.status_code == 200
    assert "event: complete" in body
    assert '"success": true' in body


def test_plan_trip_invalid_days(client):
    """travel_days 超出范围应返回 422 校验错误"""
    bad_request = {**VALID_REQUEST, "travel_days": 999}
    resp = client.post("/api/trip/plan", json=bad_request)
    assert resp.status_code == 422


def test_plan_trip_missing_field(client):
    """缺少必填字段应返回 422"""
    resp = client.post("/api/trip/plan", json={"city": "北京"})
    assert resp.status_code == 422


def test_plan_trip_failure_returns_500(monkeypatch):
    """Agent 内部异常应由全局异常处理器兜底返回 500 统一格式"""
    fake_agent = Mock()
    fake_agent.plan_trip.side_effect = RuntimeError("LLM 服务不可用")
    monkeypatch.setattr(
        "app.api.routes.trip.get_trip_planner_agent", lambda: fake_agent
    )

    # 模拟真实服务器行为: 未捕获异常不抛给客户端, 由全局处理器返回500
    no_raise_client = TestClient(app, raise_server_exceptions=False)
    resp = no_raise_client.post("/api/trip/plan", json=VALID_REQUEST)

    assert resp.status_code == 500
    data = resp.json()
    assert data["success"] is False
    assert "服务器内部错误" in data["message"]
