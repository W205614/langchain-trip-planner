"""Frozen synthetic scheduling comparison. No model/map calls or quality claims."""
import argparse
from datetime import datetime, timedelta, timezone
from itertools import permutations
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models.schemas import Attraction, DayPlan, Location, TripRequest
from app.services.time_planner import solve_day


class Routes:
    def __init__(self, missing=False):
        self.calls = 0
        self.missing = missing

    def minutes(self, left, right):
        return {(0, 1):1, (1, 2):1, (2, 3):1, (3, 0):1}.get((left, right), 5)

    def plan_route_by_locations(self, left, right, **kwargs):
        self.calls += 1
        if self.missing:
            raise TimeoutError("frozen route unavailable")
        a,b = round((left.longitude-116.30)*100), round((right.longitude-116.30)*100)
        return {"duration": self.minutes(a,b)*60, "distance":600, "walking_distance":100, "route_type":"transit"}


def run():
    now=datetime.now(timezone.utc)
    facts=[{"poi_id":p,"date":"2026-11-01","windows":[{"start":start,"end":end}],
            "source":"frozen-synthetic-v1","observed_at":now.isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat(),"confirmed":True}
           for p,start,end in (("A","11:00","13:00"),("B","09:00","10:30"),("C","09:00","20:00"))]
    req=TripRequest(city="北京",start_date="2026-11-01",end_date="2026-11-01",travel_days=1,transportation="公共交通",accommodation="酒店",
                    planning_input={"day_start":"09:00","day_end":"20:00","opening_windows":facts,
                                    "hotel_stays":[{"day_index":0,"name":"固定起点","location":{"longitude":116.30,"latitude":39.9}}]})
    day=DayPlan(day_index=0,date=req.start_date,description="冻结时间窗口样本",transportation=req.transportation,accommodation=req.accommodation,
                attractions=[Attraction(activity_id=f"poi:0:{p}",poi_id=p,name="景点"+p,address="冻结地址",location=Location(longitude=116.30+i*.01,latitude=39.9),
                                        visit_duration=45,description="冻结合成数据",optional=False) for i,p in enumerate("ABC",1)])
    # Independent travel-only baseline ignores opening windows while choosing order.
    route=Routes()
    orders=list(permutations((1,2,3)))
    baseline=min(orders,key=lambda order:sum(route.minutes(a,b) for a,b in zip((0,*order),(*order,0))))
    cursor=9*60
    windows={1:(11*60,13*60),2:(9*60,10*60+30),3:(9*60,20*60)}
    violations=[]
    previous=0
    for node in baseline:
        cursor=max(cursor+route.minutes(previous,node),windows[node][0])
        if cursor+45>windows[node][1]:violations.append(chr(64+node))
        cursor+=45
        previous=node
    cases=[]
    for name,mutation,expected in (
        ("time_windows_change_shortest_order",None,"feasible"),
        ("missing_routes_stay_unverified","missing","unverified"),
        ("expired_opening_stays_unverified","expired","unverified"),
        ("closed_mandatory_visit","closed","infeasible"),
        ("closed_optional_visit","optional","feasible"),
        ("arrival_after_daily_end","arrival","infeasible"),
    ):
        request=req.model_copy(deep=True)
        candidate=day.model_copy(deep=True)
        if mutation=="expired":
            for fact in request.planning_input.opening_windows:fact["expires_at"]=(now-timedelta(hours=1)).isoformat()
        if mutation in {"closed","optional"}:
            request.planning_input.opening_windows[1]["closed"]=True
        if mutation=="optional":candidate.attractions[1].optional=True
        if mutation=="arrival":request.planning_input.arrival_at=datetime.fromisoformat("2026-11-01T21:00:00+08:00")
        routes=Routes(missing=mutation=="missing")
        started=time.perf_counter()
        timeline,result,_=solve_day(candidate,request,routes)
        actual=result["status"]
        assert actual==expected,(name,result)
        if name=="time_windows_change_shortest_order":
            selected=[x["activity_id"] for x in timeline if x["kind"]=="attraction"]
            assert selected.index("poi:0:B")<selected.index("poi:0:A")
            assert violations,"baseline must demonstrate the motivating conflict"
        if mutation=="optional":assert "poi:0:B" in result["omitted_activity_ids"]
        cases.append({"name":name,"passed":True,"status":actual,"solver_status":result["solver_status"],
                      "seconds":round(time.perf_counter()-started,4),"fixture_route_queries":routes.calls,
                      "unknown":result.get("unknown",[]),"conflicts":result.get("conflicts",[])})
    return {"mode":"frozen_time_windows_v1","passed":True,"cases":cases,"baseline":{"method":"shortest directed travel over same candidates",
            "order":[chr(64+i) for i in baseline],"opening_window_violations":violations},
            "real_provider_calls":0,"boundary":"Six synthetic development scenarios, not live itinerary accuracy or production SLA."}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    report=run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"cases":len(report["cases"])}))
