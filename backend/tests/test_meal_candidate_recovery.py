"""Regress the saved real sample without contacting any provider."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path

import pytest

from app.models.schemas import Meal,TripPlan,TripRequest
from app.models.planning import PlanningInput
from app.services import durable_execution as durable
from app.services.planning_constraints import finalize_plan
from app.services.time_planner import ensure_activity_ids,solve_day
from test_time_planner import scenario,Routes


def test_saved_live_draft_and_paid_routes_recover_without_any_provider_send(monkeypatch):
    sample=json.loads((Path(__file__).parent/'fixtures/live_meal_route_regression.json').read_text(encoding='utf-8'))
    request,plan=TripRequest.model_validate(sample['request']),TripPlan.model_validate(sample['plan'])
    original_ids=[{a.poi_id for a in day.attractions} for day in plan.days]
    monkeypatch.setattr(durable,'read_checkpoint',lambda stage,digest:{'routes':sample['routes']} if stage=='routes' else None)
    monkeypatch.setattr(durable,'save_checkpoint',lambda *args,**kwargs:None)
    class ForbiddenProvider:
        calls=0
        def plan_route_by_locations(self,*args,**kwargs):
            self.calls+=1
            raise AssertionError('No new provider send is permitted for the saved sample')
    provider=ForbiddenProvider()
    quality=finalize_plan(plan,request,provider,enhanced=True)
    assert provider.calls==0
    assert quality['outcome']=='degraded' and quality['facts_complete'] is False
    assert [set(a.poi_id for a in day.attractions) for day in plan.days]==original_ids
    assert all(day.timeline and day.feasibility['status']=='unverified' for day in plan.days)
    assert len(quality['unmet_meals'])==5
    assert all('需就近用餐' in row['action'] for row in quality['unmet_meals'])
    assert any(gap.startswith('meal_candidate_unassigned:breakfast:') for gap in quality['data_gaps'])
    for day in plan.days:
        for row in day.timeline:
            assert datetime.fromisoformat(row['start_at']).hour>=9
            assert datetime.fromisoformat(row['end_at'])<=datetime.fromisoformat(day.date+'T19:00:00+08:00')


@pytest.mark.parametrize('protection',['explicit_window','confirmed_booking','time_lock','completed'])
def test_recommended_restaurant_cannot_be_omitted_when_user_protected(protection):
    request,plan=scenario()
    request.planning_input.day_start='09:00'
    breakfast=Meal(type='breakfast',activity_id='protected-breakfast',poi_id='breakfast',name='远端早餐候选',
        location={'longitude':117,'latitude':40})
    plan.days[0].meals=[breakfast]
    if protection=='explicit_window':
        request.planning_input.meal_windows=[{'type':'breakfast','start':'09:00','end':'10:00','duration_minutes':30}]
    elif protection=='confirmed_booking':
        request.planning_input.commitments=[{'id':'breakfast','day_index':0,'status':'confirmed','activity_id':breakfast.activity_id,
            'start_at':plan.days[0].date+'T09:00:00+08:00','end_at':plan.days[0].date+'T09:30:00+08:00'}]
    elif protection=='time_lock':
        request.planning_input.locks=[{'activity_id':breakfast.activity_id,'mode':'time','start_at':plan.days[0].date+'T09:00:00+08:00','end_at':plan.days[0].date+'T09:30:00+08:00'}]
    else:
        request.planning_input.completed_activity_ids=[breakfast.activity_id]
    class FarBreakfast(Routes):
        def plan_route_by_locations(self,left,right,**kwargs):
            return {'duration':2340 if right.longitude==117 else 600}
    timeline,report,_=solve_day(plan.days[0],request,FarBreakfast())
    assert timeline==[] and report['solver_status']=='INFEASIBLE'
    assert breakfast.activity_id not in report['omitted_activity_ids']


def test_explicit_meal_window_with_no_candidate_reports_conflict():
    request,plan=scenario()
    request.planning_input.meal_windows=[{'type':'lunch','start':'11:30','end':'13:00','duration_minutes':60}]
    timeline,report,_=solve_day(plan.days[0],request,Routes())
    assert timeline==[] and 'required_meal_candidate_missing:lunch' in report['conflicts']


def test_solver_rule_upgrade_reuses_only_unchanged_stages_and_valid_facts(monkeypatch):
    current=durable.compatibility()
    saved={**current,'rules':'time-windows-v3-hotel-nights'}
    for stage in ('draft','routes','day_draft:2026-10-30','restaurants:abc','attractions:abc','hotels:abc','weather:abc'):
        assert durable.checkpoint_compatible(stage,saved,current)
    for stage in ('schedule','schedule_day:0:abc','unrecognized'):
        assert not durable.checkpoint_compatible(stage,saved,current)
    assert not durable.checkpoint_compatible('draft',{**saved,'model':'different'},current)
    assert not durable.checkpoint_compatible('draft',{**saved,'rules':'unrecognized'},current)
    rows=[{'stage':'draft','input_hash':'digest','compatibility':saved,'payload':{'plan':'saved'},
        'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}]
    monkeypatch.setattr(durable,'_call',lambda *args,**kwargs:{'checkpoints':rows})
    with durable.durable_execution('test'):
        assert durable.read_checkpoint('draft','digest') is None
    rows[0]['expires_at']=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    with durable.durable_execution('test'):
        assert durable.read_checkpoint('draft','digest')=={'plan':'saved'}


def test_actual_v2_resume_uses_original_hashes_without_any_outbound_reservation(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app.agent_api import main
    from app.agents import trip_planner_agent
    sample=json.loads((Path(__file__).parent/'fixtures/live_meal_route_regression.json').read_text(encoding='utf-8'))
    request=TripRequest.model_validate(sample['request'])
    assert durable.input_hash(request)==sample['draft_input_hash']
    current={**sample['compatibility'],'rules':'time-windows-v4-meal-candidates'}
    monkeypatch.setattr(durable,'compatibility',lambda:current)
    expiry=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    rows=[{'stage':stage,'input_hash':sample[stage+'_input_hash'],'compatibility':sample['compatibility'],
        'expires_at':expiry,'payload':payload} for stage,payload in (
            ('draft',{'plan':sample['plan'],'trusted_candidates':{}}),('routes',{'routes':sample['routes']}))]
    sends=[]
    def journal(method,path,payload=None):
        if path.endswith('/reserve'):
            sends.append(payload['kind'])
            raise AssertionError('Provider reservations forbidden for resumed sample')
        if method=='GET': return {'checkpoints':rows}
        rows.append(payload)
        return {'success':True}
    monkeypatch.setattr(durable,'_call',journal)
    def forbidden(*args,**kwargs):
        sends.append('provider_or_generation')
        raise AssertionError('Saved draft and routes must be reused')
    planner=SimpleNamespace(plan_trip=forbidden,amap_service=SimpleNamespace(plan_route_by_locations=forbidden))
    monkeypatch.setattr(trip_planner_agent,'get_trip_planner_agent',lambda:planner)
    monkeypatch.setattr(main,'DATA_DIR',tmp_path)
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('INTERNAL_SERVICE_KEY','0123456789abcdefghijklmnopqrstuv')
    monkeypatch.setenv('BUSINESS_URL','http://journal.test')
    body={'protocol_version':2,'task_id':str(uuid4()),'execution_id':str(uuid4()),'request_id':'saved-live-recovery','user_id':1,
        'deadline_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'request':sample['request']}
    with TestClient(main.app) as client:
        response=client.post('/internal/v2/executions',json=body,headers={'X-Service-Key':'0123456789abcdefghijklmnopqrstuv'})
    assert response.status_code==200 and 'resume_draft' in response.text and 'event: result' in response.text
    assert '"outcome":"degraded"' in response.text or '"outcome": "degraded"' in response.text
    assert sends==[]
