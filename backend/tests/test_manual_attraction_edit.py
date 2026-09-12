"""Manual POI additions use the same budget, ownership and version rules as other edits."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.history import update_history
from app.core.exceptions import BizException
from app.db.models import Base, RagSyncJob
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest
from app.services.history_service import create_trip_record


def test_added_poi_is_saved_with_budget_reserve_outbox_and_version_guard(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'manual_edit.db').as_posix()}")
    Base.metadata.create_all(engine)
    request = TripRequest(city="北京", start_date="2026-09-14", end_date="2026-09-14", travel_days=1,
                          transportation="步行", accommodation="经济型酒店")
    attraction = Attraction(poi_id="P1", name="原有景点", address="北京", location=Location(longitude=116.4, latitude=39.9),
                            visit_duration=60, description="游览", ticket_price=0)
    plan = TripPlan(city=request.city, start_date=request.start_date, end_date=request.end_date,
                    overall_suggestions="测试", days=[DayPlan(date=request.start_date, day_index=0,
                    description="测试", transportation=request.transportation, accommodation=request.accommodation,
                    attractions=[attraction])])
    try:
        with Session(engine) as db:
            record = create_trip_record(db, 7, request, plan)
            initial_version = record.version
            plan.days[0].attractions.append(Attraction(poi_id="P2", name="新增景点", address="东城区",
                location=Location(longitude=116.41, latitude=39.91), visit_duration=120, description="手动添加", fact_source="amap"))
            response = update_history(record.id, plan, initial_version, db, SimpleNamespace(id=7))
            assert response["version"] == initial_version + 1
            saved = TripPlan.model_validate_json(record.plan_json)
            assert [item.poi_id for item in saved.days[0].attractions] == ["P1", "P2"]
            assert saved.budget.total_attractions > 0  # Missing price is reserved, not free.
            assert saved.budget.total_hotels == 0  # Single day requires no hotel night.
            assert "user_edited_plan_not_externally_verified" in response["quality"]["data_gaps"]
            assert db.query(RagSyncJob).count() == 2
            with pytest.raises(BizException) as conflict:
                update_history(record.id, plan, initial_version, db, SimpleNamespace(id=7))
            assert conflict.value.status_code == 409
            with pytest.raises(BizException) as forbidden:
                update_history(record.id, plan, initial_version + 1, db, SimpleNamespace(id=8))
            assert forbidden.value.status_code == 404
    finally:
        engine.dispose()
