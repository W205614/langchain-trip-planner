from datetime import datetime, timedelta, timezone
from copy import deepcopy
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError

from app.evals.constraint_benchmark import fixture
from app.models.planning import PlanningInput, RevisionScope
from app.services.time_planner import (ensure_activity_ids, fact_current, solve_day,
    plan_time_windows, protect_revision)
from app.services.planning_constraints import finalize_plan


class Routes:
    def __init__(self,missing=False):
        self.missing=missing
        self.calls=0

    def plan_route_by_locations(self,left,right,**kwargs):
        self.calls+=1
        return {} if self.missing else {"duration":600,"distance":1000,"walking_distance":1000}


def scenario(days=None):
    request,plan,_=fixture({"days":days or [["A","B"]]})
    ensure_activity_ids(plan)
    request.planning_input=PlanningInput(day_start="08:00",day_end="20:00")
    for day in plan.days:
        day.meals=[]
        day.hotel=plan.days[0].hotel
        request.planning_input.hotel_stays.append({"day_index":day.day_index,"location":{"longitude":116.1,"latitude":39.9}})
        for attraction in day.attractions:
            attraction.optional=False
            attraction.visit_duration=60
            request.planning_input.opening_windows.append({"poi_id":attraction.poi_id,"date":day.date,"windows":[{"start":"08:00","end":"20:00"}],"source":"official_fixture","observed_at":datetime.now(timezone.utc).isoformat(),"confirmed":True})
            request.planning_input.reservation_facts.append({"poi_id":attraction.poi_id,"date":day.date,"status":"not_required","source":"reservation_fixture","observed_at":datetime.now(timezone.utc).isoformat(),"expires_at":(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(),"confirmed":True})
    return request,plan


def test_known_time_windows_have_a_verifiable_timeline():
    request,plan=scenario()
    timeline,report,order=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="feasible"
    assert len(order)==2
    activities=[r for r in timeline if r["kind"]=="attraction"]
    assert all(datetime.fromisoformat(r["end_at"])-datetime.fromisoformat(r["start_at"])==timedelta(hours=1) for r in activities)
    assert report["route_minutes"]==30


def test_unknown_or_stale_openings_cannot_be_feasible():
    request,plan=scenario()
    request.planning_input.opening_windows[0]["observed_at"]=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="unverified"
    assert any("stale" in gap for gap in report["unknown"])


def test_missing_routes_are_estimated_and_never_zero_verified():
    request,plan=scenario()
    timeline,report,_=solve_day(plan.days[0],request,Routes(missing=True))
    assert report["status"]=="unverified"
    legs=[r for r in timeline if r["kind"]=="travel"]
    assert legs and all(r["status"]=="estimated" for r in legs)
    assert report["route_minutes"]==90


def test_two_fixed_appointments_cannot_overlap():
    request,plan=scenario()
    request.planning_input.commitments=[{"id":str(i),"day_index":0,"status":"confirmed","activity_id":a.activity_id,
        "start_at":f"{plan.start_date}T10:00:00+08:00","end_at":f"{plan.start_date}T11:00:00+08:00"} for i,a in enumerate(plan.days[0].attractions)]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="infeasible"
    assert report["solver_status"]=="INFEASIBLE"


def test_closed_optional_is_reported_but_required_is_not_removed():
    request,plan=scenario()
    plan.days[0].attractions[1].optional=True
    request.planning_input.opening_windows[1]["closed"]=True
    timeline,report,order=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="feasible"
    assert report["omitted_activity_ids"]==[plan.days[0].attractions[1].activity_id]
    request.constraints.must_visit=["B"]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="infeasible"
    assert report["omitted_activity_ids"]==[]


def test_confirmed_transport_is_fixed_bridge_not_a_map_edge():
    request,plan=scenario([["A"],["B"]])
    request.planning_input=PlanningInput.model_validate({**request.planning_input.model_dump(),"city_segments":[{"city":"北京","start_day":0,"end_day":0},{"city":"上海","start_day":1,"end_day":1}]})
    request.planning_input.day_windows=[type(request.planning_input).model_validate({"day_windows":[{"day_index":1,"start":"07:00","end":"20:00"}]}).day_windows[0]]
    request.planning_input.commitments=[{"id":"train","day_index":1,"category":"transport","status":"confirmed","title":"G fixture","origin_city":"北京","destination_city":"上海",
        "start_at":f"{plan.days[1].date}T08:00:00+08:00","end_at":f"{plan.days[1].date}T12:00:00+08:00",
        "origin_location":{"longitude":116,"latitude":39},"destination_location":{"longitude":121,"latitude":31}}]
    timeline,report,_=solve_day(plan.days[1],request,Routes())
    assert report["status"]=="unverified"
    assert "transport_predeparture_buffer_unverified:train" in report["unknown"]
    assert any("当前不能保证赶上班次" in note for row in timeline for note in row["notes"])
    assert any(r["activity_id"]=="commitment:train" for r in timeline)
    visit=next(r for r in timeline if r["kind"]=="attraction")
    assert datetime.fromisoformat(visit["start_at"]).hour>=12


def test_actual_finalize_pipeline_cannot_modify_another_day():
    request,plan=scenario([["A"],["B","C","D"]])
    original=plan.model_copy(deep=True)
    before=original.days[1].model_dump(mode="json")
    plan.days[1].attractions.reverse() # simulate a bad generator
    allowed=protect_revision(plan,original,request,0)
    finalize_plan(plan,request,Routes(),allowed_days=allowed,enhanced=True)
    assert plan.days[1].model_dump(mode="json")==before


def test_sparse_legacy_day_preserves_absent_fields_on_revision_serialization():
    from app.models.schemas import TripPlan
    request,plan=scenario([["A"],["B"]])
    raw=plan.model_dump(mode="json")
    for key in ("city","timeline","feasibility","generation_mode","fallback_reason","theme","activities","hotel"):
        raw["days"][1].pop(key,None)
    for key in ("optional","priority","opening_hours","fact_source","photos","requested_names"):
        raw["days"][1]["attractions"][0].pop(key,None)
    original=TripPlan.model_validate(raw)
    changed=original.model_copy(deep=True)
    allowed=protect_revision(changed,original,request,0)
    finalize_plan(changed,request,Routes(),allowed_days=allowed,enhanced=True)
    assert changed.model_dump(mode="json",exclude_unset=True)["days"][1]==raw["days"][1]


def test_partial_scope_keeps_other_content_and_existing_times():
    request,plan=scenario()
    plan_time_windows(plan,request,Routes())
    original=plan.model_copy(deep=True)
    preserved=original.days[0].attractions[0]
    fixed_time=next(row for row in original.days[0].timeline if row["activity_id"]==preserved.activity_id)
    request.planning_input=PlanningInput.model_validate({**request.planning_input.model_dump(),"revision_scope":{"day_indices":[0],"activity_ids":[original.days[0].attractions[1].activity_id]}})
    plan.days[0].attractions[0].description="unexpected rewrite"
    allowed=protect_revision(plan,original,request,0)
    plan_time_windows(plan,request,Routes(),allowed)
    assert plan.days[0].attractions[0].description==preserved.description
    updated=next(row for row in plan.days[0].timeline if row["activity_id"]==preserved.activity_id)
    assert (updated["start_at"],updated["end_at"])==(fixed_time["start_at"],fixed_time["end_at"])
    assert plan.planning_input.locks==original.planning_input.locks


def test_partial_scope_with_meals_does_not_order_afternoon_booking_before_breakfast():
    from app.models.schemas import Meal
    request,plan=scenario()
    request.planning_input.day_start="09:00"
    day=plan.days[0]
    day.meals=[Meal(type=kind,name=kind,poi_id=kind,activity_id=kind,location={"longitude":116.02+i*.001,"latitude":39.9}) for i,kind in enumerate(("breakfast","lunch","dinner"))]
    request.planning_input.commitments=[{"id":"afternoon","day_index":0,"activity_id":day.attractions[1].activity_id,"status":"confirmed",
        "start_at":f"{day.date}T15:00:00+08:00","end_at":f"{day.date}T16:00:00+08:00"}]
    plan_time_windows(plan,request,Routes())
    assert plan.days[0].timeline
    original=plan.model_copy(deep=True)
    request.planning_input.revision_scope=RevisionScope(day_indices=[0],activity_ids=[day.attractions[0].activity_id])
    allowed=protect_revision(plan,original,request,0)
    plan_time_windows(plan,request,Routes(),allowed)
    assert plan.days[0].feasibility["status"] in {"feasible","unverified"}
    old={row["activity_id"]:(row["start_at"],row["end_at"]) for row in original.days[0].timeline if row["kind"] in {"meal","attraction"}}
    new={row["activity_id"]:(row["start_at"],row["end_at"]) for row in plan.days[0].timeline if row["kind"] in {"meal","attraction"}}
    assert all(new[key]==old[key] for key in ("breakfast","lunch","dinner",original.days[0].attractions[1].activity_id))


def test_new_activity_cannot_move_into_the_gap_before_time_scope():
    request,plan=scenario([["A","B","C"]])
    day=plan.days[0]
    times=[("09:00","10:00"),("14:00","15:00"),("16:00","17:00")]
    day.timeline=[{"activity_id":a.activity_id,"kind":"attraction","start_at":f"{day.date}T{begin}:00+08:00","end_at":f"{day.date}T{end}:00+08:00"} for a,(begin,end) in zip(day.attractions,times)]
    original=plan.model_copy(deep=True)
    request.planning_input.revision_scope=RevisionScope(day_indices=[0],start_at=f"{day.date}T14:00:00+08:00",end_at=f"{day.date}T18:00:00+08:00")
    replacement=day.attractions[1].model_copy(update={"activity_id":"new-visit","poi_id":"new-poi","name":"replacement"})
    day.attractions[1]=replacement
    allowed=protect_revision(plan,original,request,0)
    plan_time_windows(plan,request,Routes(),allowed)
    row=next(r for r in plan.days[0].timeline if r["activity_id"]=="new-visit")
    assert datetime.fromisoformat(row["start_at"])>=datetime.fromisoformat(f"{day.date}T14:00:00+08:00")
    assert datetime.fromisoformat(row["end_at"])<=datetime.fromisoformat(f"{day.date}T18:00:00+08:00")


def test_full_scope_does_not_shadow_explicit_time_lock_with_a_content_lock():
    request,plan=scenario()
    day=plan.days[0]
    identity=day.attractions[0].activity_id
    request.planning_input.locks=[{"activity_id":identity,"mode":"time","start_at":f"{day.date}T14:00:00+08:00","end_at":f"{day.date}T15:00:00+08:00"}]
    original=plan.model_copy(deep=True)
    allowed=protect_revision(plan,original,request,0)
    plan_time_windows(plan,request,Routes(),allowed)
    row=next(row for row in plan.days[0].timeline if row["activity_id"]==identity)
    assert datetime.fromisoformat(row["start_at"]).hour==14
    assert datetime.fromisoformat(row["end_at"]).hour==15


def test_appointment_does_not_override_a_closed_opening_window():
    request,plan=scenario()
    request.planning_input.opening_windows[0]["closed"]=True
    request.planning_input.commitments=[{"id":"reservation","day_index":0,"status":"confirmed","activity_id":plan.days[0].attractions[0].activity_id,
        "start_at":f"{plan.start_date}T10:00:00+08:00","end_at":f"{plan.start_date}T11:00:00+08:00"}]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="infeasible"


def test_admission_window_is_not_a_departure_deadline():
    request,plan=scenario([["A"]])
    plan.days[0].attractions[0].visit_duration=120
    request.planning_input.commitments=[{"id":"entry","day_index":0,"status":"confirmed","time_mode":"entry_window","activity_id":plan.days[0].attractions[0].activity_id,
        "start_at":f"{plan.start_date}T10:00:00+08:00","end_at":f"{plan.start_date}T10:30:00+08:00"}]
    timeline,report,_=solve_day(plan.days[0],request,Routes())
    visit=next(row for row in timeline if row["kind"]=="attraction")
    assert report["status"]=="feasible"
    assert datetime.fromisoformat(visit["end_at"])-datetime.fromisoformat(visit["start_at"])==timedelta(hours=2)
    assert datetime.fromisoformat(visit["end_at"]).hour>=12


def test_legacy_booking_missing_times_is_unverified_and_planned_is_not_fixed():
    request,plan=scenario()
    request.planning_input.commitments=[{"id":1,"day_index":0,"status":"confirmed","title":"legacy"},{"id":2,"day_index":0,"status":"planned","title":"idea"}]
    request.planning_input=PlanningInput.model_validate(request.planning_input.model_dump())
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="unverified"
    assert "commitment_time_unknown:1" in report["unknown"]
    assert not any("idea" in conflict for conflict in report["conflicts"])


def test_revision_prefers_existing_times_before_shorter_route():
    request,plan=scenario()
    plan_time_windows(plan,request,Routes())
    original=plan.model_copy(deep=True)
    before={r["activity_id"]:r["start_at"] for r in original.days[0].timeline if r["kind"]=="attraction"}
    plan.days[0].attractions.reverse()
    allowed=protect_revision(plan,original,request,0)
    plan_time_windows(plan,request,Routes(),allowed)
    after={r["activity_id"]:r["start_at"] for r in plan.days[0].timeline if r["kind"]=="attraction"}
    assert before==after


def test_late_arrival_omits_outside_breakfast_instead_of_failing_whole_day():
    from app.models.schemas import Meal
    request,plan=scenario()
    day=plan.days[0]
    day.meals=[Meal(type="breakfast",name="早餐",poi_id="breakfast",activity_id="breakfast",location={"longitude":116,"latitude":39})]
    request.planning_input.arrival_at=datetime.fromisoformat(f"{day.date}T12:00:00+08:00")
    plan_time_windows(plan,request,Routes())
    assert day.feasibility["status"]!="infeasible"
    assert "breakfast" in day.feasibility["omitted_activity_ids"]
    assert day.meals==[]


def test_multicity_generation_fetches_each_city_and_preserves_global_dates(monkeypatch):
    from app.agents.trip_planner_agent import MultiAgentTripPlanner
    from app.services import rag_service
    from types import SimpleNamespace
    request,plan=scenario([["A"],["B"]])
    request.city="北京"
    request.planning_input=PlanningInput.model_validate({"city_segments":[{"city":"北京","start_day":0,"end_day":0},{"city":"上海","start_day":1,"end_day":1}]})
    calls=[]
    class Graph:
        def invoke(self,state):
            local=state["request"]
            calls.append((local.city,local.start_date,local.end_date))
            _,part=scenario([["A"]])
            part.city=local.city
            part.start_date,part.end_date=local.start_date,local.end_date
            part.days[0].date=local.start_date
            part.days[0].attractions[0].poi_id=local.city+"-poi"
            return {"trip_plan":part,"attraction_pois":[]}
    agent=object.__new__(MultiAgentTripPlanner)
    agent.graph=Graph()
    monkeypatch.setattr(rag_service,"get_rag_service",lambda:SimpleNamespace(get_attraction_rag_texts=lambda *a,**kw:{}))
    result=agent.plan_trip(request)
    assert [row[0] for row in calls]==["北京","上海"]
    assert [day.day_index for day in result.days]==[0,1]
    assert [day.date for day in result.days]==[request.start_date,request.end_date]
    assert [day.city for day in result.days]==["北京","上海"]


def test_fact_expiry_uses_earliest_declared_deadline():
    now=datetime.now(timezone.utc)
    fact={"source":"official","confirmed":True,"observed_at":(now-timedelta(hours=2)).isoformat(),"expires_at":(now-timedelta(hours=1)).isoformat()}
    assert not fact_current(fact,now=now)
    fact["expires_at"]=(now+timedelta(hours=1)).isoformat()
    assert fact_current(fact,now=now)
    fact["observed_at"]=(now+timedelta(hours=1)).isoformat()
    assert not fact_current(fact,now=now)


def test_resume_validates_hash_expiry_and_rule_compatibility(monkeypatch):
    from app.services import durable_execution as durable
    compat={"rules":"test"}
    monkeypatch.setattr(durable,"compatibility",lambda:compat)
    row={"stage":"draft","input_hash":"a"*64,"compatibility":compat,"expires_at":(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),"payload":{"known":"draft"}}
    monkeypatch.setattr(durable,"_call",lambda *args,**kwargs:{"checkpoints":[row]})
    with durable.durable_execution("test"):
        assert durable.read_checkpoint("draft","a"*64)=={"known":"draft"}
        assert durable.read_checkpoint("draft","b"*64) is None
        row["compatibility"]={"rules":"old"}
        assert durable.read_checkpoint("draft","a"*64) is None


def test_budget_fails_closed_and_new_send_has_new_reservation(monkeypatch):
    from app.services import durable_execution as durable
    calls=[]
    monkeypatch.setattr(durable,"_call",lambda method,path,payload:calls.append(payload))
    with durable.durable_execution("test"):
        durable.reserve_outbound("text")
        durable.reserve_outbound("text")
    assert len(calls)==2 and calls[0]["request_key"]!=calls[1]["request_key"]
    monkeypatch.setattr(durable,"_call",MagicMock(side_effect=RuntimeError("journal unavailable")))
    with durable.durable_execution("test"),pytest.raises(RuntimeError):
        durable.reserve_outbound("text")


def test_required_ledger_reserves_capability_calls_without_execution(monkeypatch):
    from app.services import durable_execution as durable
    calls=[]
    monkeypatch.setenv("OUTBOUND_LEDGER_REQUIRED","true")
    monkeypatch.delenv("FINAL_ACCEPTANCE_RUN_ID",raising=False)
    monkeypatch.setattr(durable,"_call",lambda *args:calls.append(args))
    durable.reserve_outbound("embedding")
    assert len(calls)==1 and calls[0][2]["execution_id"] is None


def test_v2_execution_restores_draft_without_regenerating(monkeypatch,tmp_path):
    import uuid
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.agent_api import main
    from app.services import durable_execution as durable
    from app.agents import trip_planner_agent
    request,plan=scenario()
    checkpoint_rows={}
    def journal(method,path,payload=None):
        if method=="GET":
            return {"checkpoints":list(checkpoint_rows.values())}
        checkpoint_rows[payload["stage"]]=deepcopy(payload)
        return {"success":True}
    calls=[]
    def generate(*args,**kwargs):
        calls.append("generate")
        return plan.model_copy(deep=True)
    monkeypatch.setattr(durable,"_call",journal)
    monkeypatch.setattr(trip_planner_agent,"get_trip_planner_agent",lambda:SimpleNamespace(plan_trip=generate,amap_service=Routes()))
    monkeypatch.setattr(main,"DATA_DIR",tmp_path)
    monkeypatch.delenv("DATABASE_URL",raising=False)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY","0123456789abcdefghijklmnopqrstuv")
    monkeypatch.setenv("BUSINESS_URL","http://java.test")
    body={"protocol_version":2,"task_id":str(uuid.uuid4()),"execution_id":str(uuid.uuid4()),"request_id":"resume-test","user_id":1,"deadline_at":(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),"request":request.model_dump(mode="json")}
    with TestClient(main.app) as client:
        response=client.post("/internal/v2/executions",json=body,headers={"X-Service-Key":"0123456789abcdefghijklmnopqrstuv"})
        assert response.status_code==200 and "event: result" in response.text
        assert {"draft","schedule","routes"} <= checkpoint_rows.keys()
        body["execution_id"],body["task_id"]=str(uuid.uuid4()),str(uuid.uuid4())
        resumed=client.post("/internal/v2/executions",json=body,headers={"X-Service-Key":"0123456789abcdefghijklmnopqrstuv"})
        assert "resume_draft" in resumed.text and "event: result" in resumed.text
        assert calls==["generate"]
        assert client.post("/internal/v1/executions",json=body,headers={"X-Service-Key":"0123456789abcdefghijklmnopqrstuv"}).status_code==422


@pytest.mark.parametrize("values",[{"day_start":"21:00","day_end":"09:00"},{"city_segments":[{"city":"A","start_day":0,"end_day":1},{"city":"B","start_day":1,"end_day":2}]},{"meal_windows":[{"type":"lunch","start":"12:00","end":"13:00","duration_minutes":-1}]}])
def test_invalid_inputs_rejected_before_calling_providers(values):
    with pytest.raises((ValidationError,ValueError)):
        PlanningInput.model_validate(values)


class RecordedRoutes(Routes):
    def __init__(self):
        super().__init__()
        self.edges=[]

    def plan_route_by_locations(self,left,right,**kwargs):
        self.edges.append((left.longitude,right.longitude,kwargs["city"]))
        return super().plan_route_by_locations(left,right,**kwargs)


def test_hotel_change_uses_previous_night_origin_and_current_night_destination():
    request,plan=scenario([["A"],["B"]])
    request.planning_input.hotel_stays[0]["location"]={"longitude":116.01,"latitude":39.01}
    request.planning_input.hotel_stays[1]["location"]={"longitude":116.99,"latitude":39.99}
    routes=RecordedRoutes()
    timeline,report,_=solve_day(plan.days[1],request,routes)
    attraction=plan.days[1].attractions[0]
    assert report["status"]=="feasible" and timeline
    assert routes.edges==[(116.01,attraction.location.longitude,request.city),(attraction.location.longitude,116.99,request.city)]


def intercity_hotel_scenario():
    request,plan=scenario([["A"],["B"]])
    request.planning_input=PlanningInput.model_validate({**request.planning_input.model_dump(),
        "city_segments":[{"city":"北京","start_day":0,"end_day":0},{"city":"上海","start_day":1,"end_day":1}],
        "day_windows":[{"day_index":1,"start":"07:00","end":"20:00"}]})
    request.planning_input.hotel_stays=[{"day_index":0,"location":{"longitude":116.01,"latitude":39.01}},
        {"day_index":1,"location":{"longitude":121.99,"latitude":31.99}}]
    plan.days[1].attractions[0].location=type(plan.days[1].attractions[0].location)(longitude=121.6,latitude=31.3)
    request.planning_input.commitments=[{"id":"bridge","category":"train","status":"confirmed","origin_city":"北京","destination_city":"上海",
        "start_at":f"{plan.days[1].date}T08:00:00+08:00","end_at":f"{plan.days[1].date}T12:00:00+08:00",
        "origin_location":{"longitude":116.5,"latitude":39.5},"destination_location":{"longitude":121.5,"latitude":31.5}}]
    return request,plan


def test_incoming_intercity_bridge_accounts_for_origin_city_hotel_connection():
    request,plan=intercity_hotel_scenario()
    routes=RecordedRoutes()
    timeline,report,_=solve_day(plan.days[1],request,routes)
    assert report["status"]=="unverified"
    access=next(row for row in timeline if row["activity_id"]=="connection:bridge:origin")
    assert access["start_at"].endswith("T07:50:00+08:00") and access["end_at"].endswith("T08:00:00+08:00")
    assert (116.01,116.5,"北京") in routes.edges
    assert all(left>=121 and right>=121 for left,right,city in routes.edges if city=="上海")
    assert report["route_minutes"]==30


def test_missing_origin_hotel_is_explicitly_unverified_and_cannot_be_invented():
    request,plan=intercity_hotel_scenario()
    request.planning_input.hotel_stays=request.planning_input.hotel_stays[1:]
    routes=RecordedRoutes()
    timeline,report,_=solve_day(plan.days[1],request,routes)
    assert report["status"]=="unverified"
    assert "origin_hotel_endpoint_unknown" in report["unknown"]
    assert next(row for row in timeline if row["activity_id"]=="connection:bridge:origin")["status"]=="estimated"
    assert not any(city=="北京" for _,_,city in routes.edges)


def test_missing_intercity_bridge_never_routes_previous_hotel_as_local_edge():
    request,plan=intercity_hotel_scenario()
    request.planning_input.commitments=[]
    routes=RecordedRoutes()
    _,report,_=solve_day(plan.days[1],request,routes)
    assert report["status"]=="unverified"
    assert "cross_city_confirmed_transport_missing" in report["unknown"]
    assert "previous_hotel_in_other_city" in report["unknown"]
    assert all(left>=121 and right>=121 for left,right,_ in routes.edges)


def test_previous_planned_hotel_is_used_when_no_input_override_exists():
    from app.models.schemas import Hotel
    request,plan=intercity_hotel_scenario()
    request.planning_input.hotel_stays=request.planning_input.hotel_stays[1:]
    plan.days[0].hotel=Hotel(name="可信候选住宿",location={"longitude":116.02,"latitude":39.02})
    routes=RecordedRoutes()
    _,report,_=solve_day(plan.days[1],request,routes,previous_day=plan.days[0])
    assert report["status"]=="unverified"
    assert (116.02,116.5,"北京") in routes.edges


def test_known_previous_day_overlap_cannot_be_ignored_for_origin_access():
    request,plan=intercity_hotel_scenario()
    plan.days[0].timeline=[{"activity_id":"late-return","kind":"travel","end_at":f"{plan.days[1].date}T07:55:00+08:00"}]
    timeline,report,_=solve_day(plan.days[1],request,RecordedRoutes(),previous_day=plan.days[0])
    assert timeline==[]
    assert report["status"]=="infeasible"
    assert "origin_hotel_connection_overlaps_previous_day" in report["conflicts"]


@pytest.mark.parametrize("day_start,transport_outside",[("09:00",True),("08:00",False)])
def test_early_transport_and_hotel_access_cannot_override_daily_hard_boundary(day_start,transport_outside):
    request,plan=intercity_hotel_scenario()
    request.planning_input.day_windows=[]
    request.planning_input.day_start=day_start
    timeline,report,_=solve_day(plan.days[1],request,RecordedRoutes())
    assert timeline==[] and report["status"]=="infeasible"
    assert ("fixed_transport_outside_day_window:bridge" in report["conflicts"]) is transport_outside
    assert "origin_connection_outside_day_window:bridge" in report["conflicts"]


def test_estimated_origin_route_cannot_prove_a_hard_connection_conflict():
    request,plan=intercity_hotel_scenario()
    request.planning_input.day_windows=[request.planning_input.day_windows[0].model_copy(update={'start':'07:45'})]
    timeline,report,_=solve_day(plan.days[1],request,Routes(missing=True))
    assert timeline==[] and report['status']=='unverified'
    assert 'origin_connection_outside_day_window:bridge' in report['conflicts']
    assert 'route_duration_estimated_30_minutes' in report['unknown']


def test_cached_day_transport_buffer_gap_reaches_final_quality_without_resolving(monkeypatch):
    from app.services import durable_execution as durable,time_planner
    from app.services.plan_quality import transport_to_route_type
    request,plan=intercity_hotel_scenario()
    cached={}
    for day in plan.days:
        row={'activity_id':day.attractions[0].activity_id,'kind':'attraction','title':day.attractions[0].name,
            'start_at':day.date+'T13:00:00+08:00','end_at':day.date+'T14:00:00+08:00','notes':[]}
        cached_day=day.model_copy(deep=True)
        cached_day.timeline=[row]
        if day.day_index==1:
            cached_day.timeline.insert(0,{'activity_id':'commitment:bridge','kind':'commitment','title':'已输入班次',
                'start_at':day.date+'T08:00:00+08:00','end_at':day.date+'T12:00:00+08:00','notes':[]})
        cached_day.feasibility={'status':'feasible','unknown':[],'conflicts':[],'omitted_activity_ids':[]}
        cached[day.day_index]={'day':cached_day.model_dump(mode='json')}
    def read(stage,digest):
        if stage=='routes':
            return {'routes':[{'key':[116.01,39.01,116.5,39.5,'北京',transport_to_route_type(request.transportation)],'minutes':10,'verified':True}]}
        return cached[int(stage.split(':')[1])] if stage.startswith('schedule_day:') else None
    monkeypatch.setattr(durable,'read_checkpoint',read)
    monkeypatch.setattr(durable,'save_checkpoint',lambda *args,**kwargs:None)
    def forbidden(*args,**kwargs):
        raise AssertionError('Cached day must not be solved or sent to a provider again')
    monkeypatch.setattr(time_planner,'solve_day',forbidden)
    quality=finalize_plan(plan,request,MagicMock(plan_route_by_locations=forbidden),enhanced=True)
    gap='transport_predeparture_buffer_unverified:bridge'
    assert gap in quality['data_gaps'] and any(gap in row['unknown'] for row in quality['schedule_checks'])
    assert quality['feasibility_status']=='unverified' and quality['facts_complete'] is False
    assert any(issue['code']=='TRANSPORT_BUFFER_UNVERIFIED' and '当前不能保证赶上班次' in issue['action'] for issue in quality['issues'])
