from copy import deepcopy
from datetime import datetime,timedelta,timezone
from app.services import time_planner,durable_execution
from app.services.route_facts import current
from test_time_planner import scenario


def test_near_expiry_provider_fact_survives_checkpoint_resume_without_renewal_or_new_send(monkeypatch):
    request,plan=scenario()
    original=deepcopy(plan)
    now=datetime.now(timezone.utc)
    observed=(now-timedelta(seconds=1795)).isoformat()
    expiry=(now+timedelta(seconds=5)).isoformat()
    saved={}
    monkeypatch.setattr(durable_execution,"read_checkpoint",lambda stage,digest:saved.get((stage,digest)))
    monkeypatch.setattr(durable_execution,"save_checkpoint",lambda stage,digest,payload,**kwargs:saved.__setitem__((stage,digest),deepcopy(payload)))
    class NearExpiryProvider:
        calls=0
        def plan_route_by_locations(self,*args,**kwargs):
            self.calls+=1
            return {"duration":600,"distance":1000,"observed_at":observed,"expires_at":expiry}
    provider=NearExpiryProvider()
    time_planner.plan_time_windows(plan,request,provider)
    calls=provider.calls
    assert calls>0
    assert datetime.fromisoformat(plan.days[0].route_fact_expires_at)==datetime.fromisoformat(expiry)
    rows=next(payload["routes"] for (stage,_),payload in saved.items() if stage=="routes")
    assert rows and all(row["observed_at"]==observed and row["expires_at"]==expiry for row in rows)
    monkeypatch.setattr(time_planner,"route_current",lambda value:current(value,now=now+timedelta(seconds=10)))
    time_planner.plan_time_windows(original,request,provider)
    assert provider.calls==calls
    assert datetime.fromisoformat(original.days[0].route_fact_expires_at)==datetime.fromisoformat(expiry)
    assert original.days[0].feasibility["status"]=="unverified"
    assert "route_fact_expired_or_unobserved" in original.days[0].feasibility["unknown"]


def test_legacy_checkpoint_without_observation_cannot_receive_a_new_thirty_minute_lifetime(monkeypatch):
    request,plan=scenario([["A"]])
    hotel=request.planning_input.hotel_stays[0]["location"]
    attraction=plan.days[0].attractions[0].location
    keys=[(hotel["longitude"],hotel["latitude"],attraction.longitude,attraction.latitude,request.city,"walking"),(attraction.longitude,attraction.latitude,hotel["longitude"],hotel["latitude"],request.city,"walking")]
    monkeypatch.setattr(durable_execution,"read_checkpoint",lambda stage,digest:{"routes":[{"key":list(key),"minutes":10,"verified":True} for key in keys]} if stage=="routes" else None)
    monkeypatch.setattr(durable_execution,"save_checkpoint",lambda *args,**kwargs:None)
    class ForbiddenProvider:
        def plan_route_by_locations(self,*args,**kwargs):raise AssertionError("No new provider calls for legacy recovery")
    time_planner.plan_time_windows(plan,request,ForbiddenProvider())
    assert plan.days[0].route_fact_expires_at=="1970-01-01T00:00:00+00:00"
    assert plan.days[0].feasibility["status"]=="unverified"
