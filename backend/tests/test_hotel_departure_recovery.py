"""Saved cross-city failure; new route answers are offline test injections."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.models.schemas import TripPlan,TripRequest
from app.services import durable_execution as durable
from app.services.planning_constraints import finalize_plan
from app.services.time_planner import _hotel_candidate_request,plan_time_windows


def sample(monkeypatch):
    saved=json.loads((Path(__file__).parent/'fixtures/crosscity_hotel_regression.json').read_text(encoding='utf-8'))
    request,plan=TripRequest.model_validate(saved['request']),TripPlan.model_validate(saved['plan'])
    hotel_rows={row['stage']:row for row in saved['hotel_checkpoints']}
    def read(stage,digest):
        if stage=='routes': return {'routes':saved['routes']}
        row=hotel_rows.get(stage)
        return row['payload'] if row and row['input_hash']==digest else None
    monkeypatch.setattr(durable,'read_checkpoint',read)
    monkeypatch.setattr(durable,'save_checkpoint',lambda *args,**kwargs:None)
    return saved,request,plan


class NewEdgesOnly:
    def __init__(self,saved,answer=600):
        self.old={tuple(row['key']) for row in saved['routes']}
        self.calls=[]
        self.answer=answer
    def plan_route_by_locations(self,left,right,**kwargs):
        key=(left.longitude,left.latitude,right.longitude,right.latitude,kwargs['city'],kwargs['route_type'])
        assert key not in self.old,'Successful real map edges must not be sent again'
        self.calls.append(key)
        return {} if self.answer is None else {'duration':self.answer}


def test_saved_candidates_repair_unfixed_hotel_and_only_new_edges_are_requested(monkeypatch):
    saved,request,plan=sample(monkeypatch)
    local=_hotel_candidate_request(request,0)
    assert durable.input_hash(local) in {row['input_hash'] for row in saved['hotel_checkpoints']}
    provider=NewEdgesOnly(saved)
    quality=finalize_plan(plan,request,provider,enhanced=True)
    assert quality['outcome']=='degraded' and quality['facts_complete'] is False
    assert 'hotel_prices' in quality['cost_summary']['unknown_items']
    assert plan.days[0].hotel.estimated_cost==0
    assert plan.days[0].hotel_selection['candidate_poi_id']=='B0LAZBPVEI'
    assert plan.days[0].hotel_selection['status']=='recommended_not_booked'
    assert plan.days[0].hotel.location.longitude==116.312676
    assert 0<len(provider.calls)<=51  # Edge bound; live retries still use the central budget.
    assert all(day.timeline for day in plan.days)
    assert not any('origin_connection_outside_day_window' in issue for day in plan.days for issue in day.feasibility['conflicts'])
    access=next(row for row in plan.days[1].timeline if row['activity_id']=='connection:sample-train:origin')
    assert access['start_at'].endswith('T07:50:00+08:00')
    train=next(row for row in plan.days[1].timeline if row['activity_id']=='commitment:sample-train')
    assert train['start_at'].endswith('T08:00:00+08:00') and train['end_at'].endswith('T12:30:00+08:00')


@pytest.mark.parametrize('protection',['hotel_stays','hotel_lock','confirmed_hotel','outside_scope'])
def test_hotel_repair_cannot_change_user_fixed_or_outside_scope_accommodation(monkeypatch,protection):
    saved,request,plan=sample(monkeypatch)
    if protection=='hotel_stays':
        request.planning_input.hotel_stays=[{'day_index':0,'location':plan.days[0].hotel.location.model_dump()}]
    elif protection=='hotel_lock':
        request.planning_input.locks=[{'activity_id':'hotel:0','mode':'content'}]
    elif protection=='confirmed_hotel':
        request.planning_input.commitments.append({'id':'fixed-hotel','category':'hotel','day_index':0,'status':'confirmed'})
    before=plan.days[0].model_dump(mode='json')
    provider=NewEdgesOnly(saved)
    reports=plan_time_windows(plan,request,provider,allowed_days={1} if protection=='outside_scope' else {0,1})
    if protection=='outside_scope':
        assert plan.days[0].model_dump(mode='json')==before
    else:
        assert plan.days[0].hotel.model_dump(mode='json')==before['hotel']
    arrival_report=next(report for report in reports if report['day_index']==1)
    assert plan.days[1].timeline==[]
    assert 'origin_connection_outside_day_window:sample-train' in arrival_report['conflicts']
    assert arrival_report['resolution_options'][0]['start_at'].endswith('T06:58:00+08:00')
    assert provider.calls==[]


@pytest.mark.parametrize('answer',[None,7200])
def test_unknown_or_too_long_candidate_access_keeps_conflict_and_clear_choice(monkeypatch,answer):
    saved,request,plan=sample(monkeypatch)
    original=plan.days[0].hotel.model_dump()
    provider=NewEdgesOnly(saved,answer)
    reports=plan_time_windows(plan,request,provider)
    assert plan.days[0].hotel.model_dump()==original
    assert plan.days[1].timeline==[]
    assert reports[1]['resolution_options'][1]['action']=='choose_other_hotel'
    assert 'origin_connection_outside_day_window:sample-train' in reports[1]['conflicts']
    assert len(provider.calls)==3


def test_scoped_quality_retains_meal_gaps_from_untouched_days(monkeypatch):
    saved,request,plan=sample(monkeypatch)
    untouched=plan.days[0]
    untouched.feasibility={'status':'unverified','unmet_meals':[{'activity_id':'prior-breakfast','type':'breakfast','title':'旧早餐','action':'需就近用餐'}]}
    before=untouched.model_dump(mode='json')
    quality=finalize_plan(plan,request,NewEdgesOnly(saved),enhanced=True,allowed_days={1})
    assert plan.days[0].model_dump(mode='json')==before
    assert any(row['activity_id']=='prior-breakfast' and row['day_index']==0 for row in quality['unmet_meals'])
    assert 'meal_candidate_unassigned:breakfast:prior-breakfast' in quality['data_gaps']


def test_actual_v2_crosscity_resume_reuses_original_generation_and_hotel_stage_hashes(monkeypatch,tmp_path):
    from datetime import datetime,timedelta,timezone
    from types import SimpleNamespace
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app.agent_api import main
    from app.agents import trip_planner_agent
    from app.services.time_planner import RULE_VERSION
    saved=json.loads((Path(__file__).parent/'fixtures/crosscity_hotel_regression.json').read_text(encoding='utf-8'))
    request=TripRequest.model_validate(saved['request'])
    assert durable.input_hash(request)==saved['draft_input_hash']
    monkeypatch.setattr(durable,'compatibility',lambda:{**saved['compatibility'],'rules':RULE_VERSION})
    expiry=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    rows=[{'stage':stage,'input_hash':saved[stage+'_input_hash'],'compatibility':saved['compatibility'],
        'expires_at':expiry,'payload':payload} for stage,payload in (
            ('draft',{'plan':saved['plan'],'trusted_candidates':{}}),('routes',{'routes':saved['routes']}))]
    rows.extend({**row,'expires_at':expiry} for row in saved['hotel_checkpoints'])
    generation=[]
    def journal(method,path,payload=None):
        assert not path.endswith('/reserve'),'Only the injected new map edges may run offline'
        if method=='GET': return {'checkpoints':rows}
        rows.append(payload)
        return {'success':True}
    def forbidden_generation(*args,**kwargs):
        generation.append(True)
        raise AssertionError('Old paid model/RAG generation must not rerun')
    provider=NewEdgesOnly(saved)
    monkeypatch.setattr(durable,'_call',journal)
    monkeypatch.setattr(trip_planner_agent,'get_trip_planner_agent',lambda:SimpleNamespace(plan_trip=forbidden_generation,amap_service=provider))
    monkeypatch.setattr(main,'DATA_DIR',tmp_path)
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('INTERNAL_SERVICE_KEY','0123456789abcdefghijklmnopqrstuv')
    monkeypatch.setenv('BUSINESS_URL','http://journal.test')
    body={'protocol_version':2,'task_id':str(uuid4()),'execution_id':str(uuid4()),'request_id':'saved-crosscity-recovery','user_id':1,
        'deadline_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'request':saved['request']}
    with TestClient(main.app) as client:
        response=client.post('/internal/v2/executions',json=body,headers={'X-Service-Key':'0123456789abcdefghijklmnopqrstuv'})
    assert response.status_code==200 and 'resume_draft' in response.text and 'event: result' in response.text
    assert '"outcome":"degraded"' in response.text or '"outcome": "degraded"' in response.text
    assert generation==[] and 0<len(provider.calls)<=51
