from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest

from app.services import durable_execution, time_planner
from test_time_planner import scenario


@pytest.mark.parametrize("stage,expected", [
    ("draft", True), ("routes", True), ("day_draft:date", True),
    ("attractions:digest", True), ("hotels:digest", True), ("restaurants:digest", True),
    ("weather:digest", True), ("schedule", False), ("schedule_day:0:digest", False),
])
def test_v10_reuses_provider_evidence_but_recomputes_old_schedules(stage, expected):
    saved = {"rules": "time-windows-v9-route-evidence", "model": "fixture", "prompt": "same"}
    current = {**saved, "rules": "time-windows-v10-adjustment-resume"}
    assert durable_execution.checkpoint_compatible(stage, saved, current) is expected
    assert not durable_execution.checkpoint_compatible(stage, saved, {**current, "model": "changed"})
    assert not durable_execution.checkpoint_compatible(stage, saved, {**current, "prompt": "changed"})


def journal(monkeypatch):
    saved={}
    monkeypatch.setattr(durable_execution,"read_checkpoint",lambda stage,digest:saved.get((stage,digest)))
    monkeypatch.setattr(durable_execution,"save_checkpoint",lambda stage,digest,payload,**kwargs:saved.__setitem__((stage,digest),deepcopy(payload)))
    return saved


def test_v2_resume_preserves_enrichment_notices_and_paid_route_checkpoint_hash(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from app.agent_api import main
    from app.agents import trip_planner_agent
    from app.services.execution import rag_degradation_var
    from test_time_planner import Routes
    request,plan=scenario()
    plan.enrichment_notices=['existing enrichment notice']
    rows={};generations=[]
    def journal_call(method,path,payload=None):
        if method=='GET':return {'checkpoints':list(rows.values())}
        rows[payload['stage']]=deepcopy(payload)
        return {'success':True}
    def generate(*args,**kwargs):
        generations.append(True)
        rag_degradation_var.get().append('retrieval unavailable notice')
        return plan.model_copy(deep=True)
    provider=Routes()
    monkeypatch.setattr(durable_execution,'_call',journal_call)
    monkeypatch.setattr(trip_planner_agent,'get_trip_planner_agent',lambda:SimpleNamespace(plan_trip=generate,amap_service=provider))
    monkeypatch.setattr(main,'DATA_DIR',tmp_path)
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('INTERNAL_SERVICE_KEY','0123456789abcdefghijklmnopqrstuv')
    monkeypatch.setenv('BUSINESS_URL','http://journal.test')
    body={'protocol_version':2,'task_id':str(uuid4()),'execution_id':str(uuid4()),'request_id':'notice-resume-test','user_id':1,
        'deadline_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'request':request.model_dump(mode='json')}
    with TestClient(main.app) as client:
        first=client.post('/internal/v2/executions',json=body,headers={'X-Service-Key':'0123456789abcdefghijklmnopqrstuv'})
        assert first.status_code==200 and 'event: result' in first.text
        paid_calls=provider.calls
        first_routes=deepcopy(rows['routes'])
        assert rows['draft']['payload']['plan']['enrichment_notices']==['existing enrichment notice','retrieval unavailable notice']
        body.update(task_id=str(uuid4()),execution_id=str(uuid4()))
        resumed=client.post('/internal/v2/executions',json=body,headers={'X-Service-Key':'0123456789abcdefghijklmnopqrstuv'})
    assert resumed.status_code==200 and 'resume_draft' in resumed.text and 'event: result' in resumed.text
    assert generations==[True] and provider.calls==paid_calls
    assert rows['routes']['input_hash']==first_routes['input_hash']
    assert rows['routes']['payload']==first_routes['payload']


def test_default_lookup_limit_defers_an_unsent_route(monkeypatch):
    monkeypatch.delenv("PLANNER_ROUTE_LOOKUPS",raising=False)
    request,plan=scenario()
    left,right=[a.location for a in plan.days[0].attractions]
    calls=[]
    provider=SimpleNamespace(plan_route_by_locations=lambda *args,**kwargs:calls.append(args))
    cache={"_lookups":120}
    unknown=set()

    result=time_planner._route(left,right,provider,request.city,"walking",cache,unknown)

    assert result==(30,False)
    assert calls==[]
    assert "route_lookup_deferred" in unknown
    assert "route_failure_not_retried_on_resume" not in unknown
    assert (left.longitude,left.latitude,right.longitude,right.latitude,request.city,"walking") in cache["_deferred_routes"]


def test_default_thirty_second_stage_resumes_unsent_edges_without_recharging_paid_edges(monkeypatch):
    monkeypatch.delenv("PLANNER_TOTAL_SECONDS",raising=False)
    monkeypatch.delenv("PLANNER_ROUTE_LOOKUPS",raising=False)
    saved=journal(monkeypatch)
    request,plan=scenario([["A","B"],["C","D"]])
    original=plan.model_copy(deep=True)
    clock=SimpleNamespace(value=0)
    monkeypatch.setattr(time_planner,"time",SimpleNamespace(monotonic=lambda:clock.value))

    class Routes:
        def __init__(self):
            self.calls=[]

        def plan_route_by_locations(self,left,right,**kwargs):
            self.calls.append((left.longitude,left.latitude,right.longitude,right.latitude,kwargs["city"],kwargs["route_type"]))
            if len(self.calls)==1:
                clock.value+=31
            return {"duration":600,"distance":1000,"walking_distance":1000}

    provider=Routes()
    time_planner.plan_time_windows(plan,request,provider)
    first_keys=list(provider.calls)
    route_keys=[key for key in saved if key[0]=="routes"]
    assert len(route_keys)==1
    first_rows=deepcopy(saved[route_keys[0]]["routes"])
    assert first_keys and len(first_keys)==1
    assert first_rows and {tuple(row["key"]) for row in first_rows}==set(first_keys)
    assert any(day.feasibility.get("solver_status")=="TOTAL_BUDGET_EXHAUSTED" for day in plan.days)

    time_planner.plan_time_windows(original,request,provider)

    assert [key for key in saved if key[0]=="routes"]==route_keys
    assert len(provider.calls)>len(first_keys)
    assert all(provider.calls.count(key)==1 for key in first_keys)
    assert all(day.feasibility.get("solver_status")!="TOTAL_BUDGET_EXHAUSTED" for day in original.days)
    assert all("route_lookup_deferred" not in day.feasibility.get("unknown",[]) for day in original.days)


def test_an_attempted_failed_route_remains_failed_without_an_implicit_retry(monkeypatch):
    monkeypatch.delenv("PLANNER_TOTAL_SECONDS",raising=False)
    monkeypatch.delenv("PLANNER_ROUTE_LOOKUPS",raising=False)
    saved=journal(monkeypatch)
    request,plan=scenario()
    original=plan.model_copy(deep=True)

    class Routes:
        def __init__(self):
            self.calls=[]

        def plan_route_by_locations(self,left,right,**kwargs):
            key=(left.longitude,left.latitude,right.longitude,right.latitude,kwargs["city"],kwargs["route_type"])
            self.calls.append(key)
            return {} if key==self.calls[0] else {"duration":600,"distance":1000,"walking_distance":1000}

    provider=Routes()
    time_planner.plan_time_windows(plan,request,provider)
    failed=provider.calls[0]
    rows=next(value["routes"] for (stage,_),value in saved.items() if stage=="routes")
    assert any(tuple(row["key"])==failed and not row["verified"] for row in rows)

    time_planner.plan_time_windows(original,request,provider)

    assert provider.calls.count(failed)==1
    restored_rows=next(value["routes"] for (stage,_),value in saved.items() if stage=="routes")
    assert next(row for row in restored_rows if tuple(row["key"])==failed)==next(row for row in rows if tuple(row["key"])==failed)
