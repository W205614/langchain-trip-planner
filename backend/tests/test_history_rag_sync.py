"""历史记录修改后，派生 RAG 向量必须随之重建。"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db import database as dbmod
from app.db import models as models_mod
from app.models.schemas import Attraction, DayPlan, Location, TripPlan


def _plan() -> TripPlan:
    return TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", overall_suggestions="测试",
        days=[DayPlan(
            date="2026-08-01", day_index=0, description="测试", transportation="公共交通", accommodation="经济型酒店",
            attractions=[Attraction(
                poi_id="B000A8UIN9", name="故宫博物院", address="景山前街4号",
                location=Location(longitude=116.397, latitude=39.918), visit_duration=120, description="测试",
            )],
            meals=[],
        )],
    )


@pytest.fixture()
def auth_client(tmp_path):
    """独立临时数据库，避免历史同步测试依赖其他测试模块的夹具。"""
    db_path = tmp_path / "history_sync.db"
    engine = dbmod.create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    session_local = dbmod.sessionmaker(bind=engine, autoflush=False, autocommit=False)
    models_mod.Base.metadata.create_all(bind=engine)

    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[dbmod.get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_history_update_replaces_derived_rag_vector(auth_client, monkeypatch):
    from app.api.routes import history as history_routes
    from app.api.routes import trip as trip_routes

    fake_agent = MagicMock()
    fake_agent.plan_trip.return_value = _plan()
    fake_rag = MagicMock()
    monkeypatch.setattr(trip_routes, "get_trip_planner_agent", lambda: fake_agent)
    monkeypatch.setattr(trip_routes, "get_rag_service", lambda: fake_rag)
    monkeypatch.setattr(history_routes, "get_rag_service", lambda: fake_rag)

    token = auth_client.post("/api/auth/register", json={"username": "history-user", "password": "secret123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "city": "北京", "start_date": "2026-08-01", "end_date": "2026-08-01", "travel_days": 1,
        "transportation": "公共交通", "accommodation": "经济型酒店", "preferences": [], "free_text_input": "",
    }
    assert auth_client.post("/api/trip/plan", json=request, headers=headers).status_code == 200

    updated = _plan().model_dump(mode="json")
    updated["overall_suggestions"] = "已编辑"
    response = auth_client.put("/api/history/1", json=updated, headers=headers)

    assert response.status_code == 200
    fake_rag.delete_history_plan.assert_any_call(1, 1)
    fake_rag.add_history_plan.assert_called()
    args = fake_rag.add_history_plan.call_args.args
    assert args[0:2] == (1, 1)
    assert args[3].overall_suggestions == "已编辑"
