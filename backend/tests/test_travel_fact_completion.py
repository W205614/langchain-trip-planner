from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
from app.models.planning import PlanningInput
from app.services.time_planner import solve_day, fact_current, plan_time_windows
from app.services.travel_facts import boarding_minutes
from test_time_planner import scenario, Routes


def fact(**values):
    now=datetime.now(timezone.utc)
    return dict(source="user-provided-carrier-or-venue-notice",confirmed=True,observed_at=now.isoformat(),expires_at=(now+timedelta(hours=5)).isoformat(),**values)


def test_fact_source_and_expiry_are_required_and_user_authority_is_explicit():
    with pytest.raises(ValidationError):
        PlanningInput(reservation_facts=[{"poi_id":"p","date":"2026-10-26","status":"confirmed"}])
    inputs=PlanningInput(reservation_facts=[fact(poi_id="p",date="2026-10-26",status="confirmed",authority="official")])
    assert inputs.reservation_facts[0]["authority"]=="user_declared"
    duplicate=inputs.reservation_facts[0].copy()
    with pytest.raises(ValidationError):
        PlanningInput(reservation_facts=[duplicate,duplicate])


def test_reservation_unknown_and_unavailable_do_not_appear_as_feasible():
    request,plan=scenario()
    request.planning_input.reservation_facts=[]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="unverified"
    assert any(gap.startswith("reservation_unverified:") for gap in report["unknown"])
    request.planning_input.reservation_facts=[fact(poi_id=a.poi_id,date=plan.days[0].date,status="unavailable") for a in plan.days[0].attractions]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="infeasible"


def test_inline_transport_identity_survives_persistence_and_expired_buffer_does_not_resolve_gap():
    request,_=scenario()
    request.planning_input.transport_buffers=[fact(commitment_id="inline-uuid",minutes=45)]
    assert boarding_minutes(request.planning_input,{"id":123,"input_commitment_id":"inline-uuid"},fact_current)==45
    request.planning_input.transport_buffers[0]["expires_at"]=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert boarding_minutes(request.planning_input,{"id":123,"input_commitment_id":"inline-uuid"},fact_current) is None


def test_known_included_parent_child_cannot_be_counted_as_two_independent_visits():
    request,plan=scenario()
    left,right=plan.days[0].attractions
    request.planning_input.poi_relations=[fact(parent_poi_id=left.poi_id,child_poi_id=right.poi_id,relation="included")]
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="infeasible"
    request.planning_input.poi_relations[0]["relation"]="separate"
    _,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="feasible"


def test_cross_day_nested_visits_are_flagged_without_rewriting_an_out_of_scope_day():
    request,plan=scenario([["A"],["B"]])
    left,right=[day.attractions[0] for day in plan.days]
    request.planning_input.poi_relations=[fact(parent_poi_id=left.poi_id,child_poi_id=right.poi_id,relation="included")]
    before=plan.days[0].model_dump(mode="json")
    reports=plan_time_windows(plan,request,Routes(),allowed_days={1})
    assert plan.days[0].model_dump(mode="json")==before
    assert reports[0]["status"]=="infeasible"
    assert "parent_child_duplicate_visit" in reports[0]["conflicts"]


def test_self_arranged_meal_supplies_real_endpoint_and_time_without_inventing_restaurant():
    request,plan=scenario()
    request.planning_input.meal_windows=[{"type":"lunch","start":"11:30","end":"14:00","duration_minutes":30}]
    request.planning_input.meal_resolutions=[fact(day_index=0,type="lunch",mode="self_arranged",location={"longitude":116.1,"latitude":39.9})]
    timeline,report,_=solve_day(plan.days[0],request,Routes())
    assert report["status"]=="feasible"
    meal=next(row for row in timeline if row["activity_id"]=="self_meal:0:lunch")
    assert meal["source"].startswith("用户声明：")
    assert plan.days[0].meals==[]


def test_confirmed_boarding_lead_time_is_a_hard_occupancy_and_not_just_a_dismissed_warning():
    request,plan=scenario([["A"]])
    day=plan.days[0];activity=day.attractions[0]
    request.city="北京"
    request.planning_input.locks=[{"activity_id":activity.activity_id,"mode":"time","start_at":f"{day.date}T10:40:00+08:00","end_at":f"{day.date}T11:40:00+08:00"}]
    request.planning_input.commitments=[{"id":"train","day_index":0,"category":"transport","status":"confirmed","title":"用户确认交通","origin_city":"北京","destination_city":"上海","start_at":f"{day.date}T12:00:00+08:00","end_at":f"{day.date}T14:00:00+08:00","origin_location":{"longitude":116.1,"latitude":39.9},"destination_location":{"longitude":121,"latitude":31}}]
    _,before,_=solve_day(day,request,Routes())
    assert before["status"]=="unverified"
    request.planning_input.transport_buffers=[fact(commitment_id="train",minutes=30)]
    _,after,_=solve_day(day,request,Routes())
    assert after["status"]=="infeasible"
    assert "transport_predeparture_buffer_unverified:train" not in after["unknown"]
