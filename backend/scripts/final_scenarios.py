"""Core planning scenarios through real HTTP, strictly offline validation only.

Providers are fixture substitutes; this proves business integration, not travel
fact quality. The marker is checked before registration or any other mutation.
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote,urlparse
from uuid import uuid4

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evals.scenario_contracts import compare_excluded_day


def run(base_url,output,cross_city_only=False):
    parsed=urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1","localhost","::1","frontend","backend"}:
        raise RuntimeError("This fixture runner only targets a local isolated stack")
    report={"mode":"offline_cross_city_hotel_http" if cross_city_only else "offline_core_scenarios_http","passed":False,"provider_calls":0,"checks":[],"started_at":datetime.now(timezone.utc).isoformat(),
        "boundary":"Real Java/Python/PostgreSQL HTTP; model and map are offline provider fixtures. No live quality or capacity claim."}
    with httpx.Client(base_url=base_url,timeout=45,trust_env=False) as client:
        def req(method,path,expected=(200,),label=None,**kwargs):
            response=client.request(method,path,**kwargs)
            if response.status_code not in expected:
                detail="authentication response withheld" if path.startswith("/api/auth/") else response.text[:1600]
                raise AssertionError(f"{label or path}: HTTP {response.status_code}: {detail}")
            if label:
                report["checks"].append({"name":label,"passed":True,"http_status":response.status_code})
            return response.json() if response.content else {}
        def check(name,condition):
            if not condition:
                raise AssertionError(name)
            report["checks"].append({"name":name,"passed":True})
        def task(identity):
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                state=req("GET",f"/api/trip/tasks/{identity}")["data"]
                if state["status"] in {"succeeded","needs_attention","failed","cancelled"}:
                    if state["status"] not in {"succeeded","needs_attention"}:
                        report["failed_task"]={key:state.get(key) for key in ("id","status","error_code","message")}
                        raise AssertionError(f"Task {state['status']}: {state.get('error_code')} {state.get('message')}")
                    check("task reaches valid successful terminal",True)
                    return state
                time.sleep(.25)
            raise AssertionError("Task remained nonterminal")
        def history(identity):
            return req("GET",f"/api/history/{identity}")["data"]
        def planning(identity):
            return req("GET",f"/api/trips/{identity}/planning-input")["data"]
        def propose(identity,day,scope,content):
            before=history(identity)
            current=planning(identity)
            conversation=req("POST","/api/assistant/conversations",json={"active_trip_id":identity})["id"]
            created=req("POST",f"/api/assistant/conversations/{conversation}/messages",(202,),json={"mode":"revise","content":content,
                "record_id":identity,"version":before["version"],"day_index":day,"planning_revision":current["planning_revision"],"revision_scope":scope},headers={"Idempotency-Key":str(uuid4())})
            state=task(created["task_id"])
            check("revision is a separate pending proposal",state["status"]=="needs_attention")
            proposal=history(state["result"]["id"])
            check("normal revision has no blocking draft outcome",proposal["quality"].get("validated_outcome") in {"complete","degraded"})
            current_record=history(identity)
            check("preview has no write-through",current_record["version"]==before["version"] and current_record["plan"]==before["plan"])
            return conversation,proposal,before
        def confirm(conversation,proposal):
            url=f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/confirm"
            req("POST",url,json={},headers={"If-Match":str(proposal["version"])},label="owner confirms previewed proposal")
            req("POST",url,(409,),json={},headers={"If-Match":str(proposal["version"])},label="repeat confirmation conflicts")
        try:
            marker=req("GET","/api/validation/fixture")
            if marker.get("offline_fixture") is not True:
                raise RuntimeError("Refusing a deployment without explicit offline fixture marker")
            registered=req("POST","/api/auth/register",json={"username":"scenarios_"+uuid4().hex[:10],"password":"isolated-scenarios123"})
            client.headers["Authorization"]="Bearer "+registered["access_token"]
            request={"city":"北京","start_date":"2026-11-01","end_date":"2026-11-03","travel_days":3,"transportation":"公共交通","accommodation":"经济型酒店","preferences":[],
                "planning_input":{"day_start":"09:00","day_end":"20:00","timezone":"Asia/Shanghai","city_segments":[{"city":"北京","start_day":0,"end_day":0},{"city":"上海","start_day":1,"end_day":2}],
                    "day_windows":[{"day_index":1,"start":"07:00","end":"20:00"}],
                    "hotel_stays":[{"day_index":0,"city":"北京","name":"用户选择的前晚北京住宿","location":{"longitude":116.41,"latitude":39.91}}],
                    "commitments":[{"id":"train-fixture","activity_id":"commitment:train-fixture","day_index":1,"category":"transport","status":"confirmed","title":"用户确认的跨城班次","time_mode":"fixed",
                        "origin_city":"北京","destination_city":"上海","origin_name":"北京站","destination_name":"上海站","origin_location":{"longitude":116.4,"latitude":39.9},"destination_location":{"longitude":121.47,"latitude":31.23},
                        "start_at":"2026-11-02T08:00:00+08:00","end_at":"2026-11-02T12:00:00+08:00","amount_cents":10000}]}}
            malformed=deepcopy(request)
            malformed["planning_input"]["city_segments"][1]["start_day"]=0
            req("POST","/api/trip/tasks",(422,),json=malformed,headers={"Idempotency-Key":str(uuid4())},label="overlapping city segments rejected")
            state=task(req("POST","/api/trip/tasks",(202,),json=request,headers={"Idempotency-Key":str(uuid4())})["data"]["id"])
            check("normal cross-city generation succeeds",state["status"]=="succeeded")
            identity=state["result"]["id"]
            record=history(identity)
            check("normal cross-city plan has no blocking draft outcome",record["quality"].get("outcome") in {"complete","degraded"})
            days=record["plan"]["days"]
            check("city segmentation preserved",[day["city"] for day in days]==["北京","上海","上海"])
            check("cross-city POIs belong to each city",all("beijing" in a["poi_id"] for a in days[0]["attractions"]) and all("shanghai" in a["poi_id"] for d in days[1:] for a in d["attractions"]))
            arrivals=[row for row in days[1]["timeline"] if row["kind"]=="attraction"]
            check("arrival bounds local visits",bool(arrivals) and all(datetime.fromisoformat(row["start_at"])>=datetime.fromisoformat("2026-11-02T12:00:00+08:00") for row in arrivals))
            check("confirmed transport retained in timeline",any(row["activity_id"]=="commitment:train-fixture" and datetime.fromisoformat(row["end_at"])==datetime.fromisoformat("2026-11-02T12:00:00+08:00") for row in days[1]["timeline"]))
            check("previous-night origin hotel connection is explicitly scheduled",any(row["activity_id"].startswith("connection:") and row["kind"]=="travel" and row["status"]=="confirmed" and datetime.fromisoformat(row["start_at"])==datetime.fromisoformat("2026-11-02T07:50:00+08:00") and datetime.fromisoformat(row["end_at"])==datetime.fromisoformat("2026-11-02T08:00:00+08:00") for row in days[1]["timeline"]))
            check("unknown facts are not certified complete",record["quality"].get("facts_complete") is False)
            if cross_city_only:
                check("intermediate city final day retains trusted hotel recommendation",bool(days[0].get("hotel")) and days[0]["hotel"]["location"]["longitude"]<120)
                check("whole-trip final day adds no extra recommended hotel night",days[-1].get("hotel") is None)
                report.update(passed=True,trip_id=identity,checks_passed=len(report["checks"]))
                return report

            booked=days[2]["attractions"][0]
            req("POST",f"/api/trips/{identity}/commitments",json={"day_index":2,"category":"ticket","status":"confirmed","title":"确认预约","note":"离线验收固定占用时段","amount":"60.00",
                "activity_id":booked["activity_id"],"poi_id":booked["poi_id"],"location":booked["location"],"time_mode":"fixed","start_at":"2026-11-03T15:00:00+08:00","end_at":"2026-11-03T16:00:00+08:00"},headers={"Idempotency-Key":str(uuid4())},label="owner records a confirmed appointment")
            check("ledger yuan input persists as integer cents",any(c.get("activity_id")==booked["activity_id"] and c.get("amount_cents")==6000 for c in planning(identity)["planning_input"]["commitments"]))
            conversation,proposal,before=propose(identity,2,{"day_indices":[2]},"调整第3天并保留所有已确认预约")
            fixed=next(row for row in proposal["plan"]["days"][2]["timeline"] if row["activity_id"]==booked["activity_id"])
            check("new appointment time controls rescheduling",datetime.fromisoformat(fixed["start_at"])==datetime.fromisoformat("2026-11-03T15:00:00+08:00") and datetime.fromisoformat(fixed["end_at"])==datetime.fromisoformat("2026-11-03T16:00:00+08:00"))
            check("full-day revision leaves other cities and dates unchanged",proposal["plan"]["days"][:2]==before["plan"]["days"][:2])
            confirm(conversation,proposal)
            after=history(identity)
            check("appointment confirmation advances one version",after["version"]==before["version"]+1)

            target_day=after["plan"]["days"][2]
            mutable=next(a for a in target_day["attractions"] if a["activity_id"]!=booked["activity_id"])
            # Keep this scenario's selected current activity, but identify it
            # explicitly to the provider fixture instead of assuming a slot.
            mutable=target_day["attractions"][1] if target_day["attractions"][1]["activity_id"]!=booked["activity_id"] else mutable
            scope={"day_indices":[2],"activity_ids":[mutable["activity_id"]]}
            conversation,proposal,before=propose(identity,2,scope,f"只替换指定活动 fixture:replace={mutable['poi_id']}，保留其他活动及预约")
            old_by_id={a["activity_id"]:a for a in before["plan"]["days"][2]["attractions"]}
            new_by_id={a["activity_id"]:a for a in proposal["plan"]["days"][2]["attractions"]}
            check("partial scope actually replaces selected activity",mutable["activity_id"] not in new_by_id and len(new_by_id)==len(old_by_id))
            check("partial scope preserves all other activity content",all(new_by_id.get(key)==value for key,value in old_by_id.items() if key!=mutable["activity_id"]))
            old_times={r["activity_id"]:(r["start_at"],r["end_at"]) for r in before["plan"]["days"][2]["timeline"] if r["kind"] in {"attraction","meal"} and r["activity_id"]!=mutable["activity_id"]}
            new_times={r["activity_id"]:(r["start_at"],r["end_at"]) for r in proposal["plan"]["days"][2]["timeline"] if r["kind"] in {"attraction","meal"}}
            check("partial scope preserves other activity and meal times",all(new_times.get(key)==value for key,value in old_times.items()))
            check("partial scope preserves non-target dates exactly",proposal["plan"]["days"][:2]==before["plan"]["days"][:2])
            confirm(conversation,proposal)
            check("partial confirmation advances one version",history(identity)["version"]==before["version"]+1)

            current=history(identity)
            third=current["plan"]["days"][2]
            selected=next(row for row in third["timeline"] if row["kind"]=="attraction" and row["activity_id"]!=booked["activity_id"])
            conversation,proposal,before=propose(identity,2,{"day_indices":[2],"start_at":selected["start_at"],"end_at":selected["end_at"]},"仅调整所选时间段，保留其他安排")
            cfg=planning(identity)
            changed=deepcopy(cfg["planning_input"])
            changed["day_end"]="20:30"
            req("PUT",f"/api/trips/{identity}/planning-input",json={"planning_input":changed,"planning_revision":cfg["planning_revision"]},headers={"If-Match":str(cfg["trip_version"])},label="owner changes dependency after preview")
            req("POST",f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/confirm",(409,),json={},headers={"If-Match":str(proposal["version"])},label="stale planning dependency blocks old proposal")
            check("stale confirmation does not change trip version",history(identity)["version"]==before["version"])

            conversation,proposal,before=propose(identity,0,{"day_indices":[0,2]},"仅调整第1天和第3天 fixture:replace，第2天及所有预约保持不变")
            excluded_contract=compare_excluded_day(before["plan"]["days"][1],proposal["plan"]["days"][1])
            report["excluded_middle_day_projection_contract"]=excluded_contract
            check("excluded middle day remains stale and unverified in both records",excluded_contract["stale_unverified_on_both"])
            check("multi-day authorization preserves excluded middle day exactly",excluded_contract["passed"])
            for index in (0,2):
                check(f"authorized day {index} actually changes its selected activities",{a["activity_id"] for a in proposal["plan"]["days"][index]["attractions"]}!={a["activity_id"] for a in before["plan"]["days"][index]["attractions"]})
            confirmed_before=next(row for row in before["plan"]["days"][2]["timeline"] if row["activity_id"]==booked["activity_id"])
            confirmed_after=next(row for row in proposal["plan"]["days"][2]["timeline"] if row["activity_id"]==booked["activity_id"])
            check("multi-day revision preserves confirmed appointment time",all(confirmed_before[key]==confirmed_after[key] for key in ("start_at","end_at")))
            confirm(conversation,proposal)
            check("multi-day confirmation advances one version",history(identity)["version"]==before["version"]+1)

            current=history(identity)
            day=current["plan"]["days"][2]
            timeline={row["activity_id"]:row for row in day["timeline"] if row["kind"]=="attraction"}
            ordinary=sorted([a for a in day["attractions"] if a["activity_id"]!=booked["activity_id"]],key=lambda a:timeline[a["activity_id"]]["start_at"])
            check("remaining-day scenario has an executed prefix and a mutable visit",len(ordinary)>=2)
            completed,remaining=ordinary[0],ordinary[1]
            cfg=planning(identity)
            req("POST",f"/api/trips/{identity}/activities/{quote(completed['activity_id'],safe='')}/state",json={"planning_revision":cfg["planning_revision"],"locked":True,"lock_mode":"time","completed":True},headers={"If-Match":str(cfg["trip_version"])},label="owner marks first activity completed and time-locked")
            cutoff=timeline[remaining["activity_id"]]["start_at"]
            end=f"{day['date']}T20:30:00+08:00"
            conversation,proposal,before=propose(identity,2,{"day_indices":[2],"start_at":cutoff,"end_at":end},f"从所选时刻开始调整当天剩余安排 fixture:replace={remaining['poi_id']}，已完成和锁定活动以及预约不得变化")
            old_day,new_day=before["plan"]["days"][2],proposal["plan"]["days"][2]
            old_activities={a["activity_id"]:a for a in [*old_day["attractions"],*old_day["meals"]]}
            new_activities={a["activity_id"]:a for a in [*new_day["attractions"],*new_day["meals"]]}
            old_timeline={r["activity_id"]:r for r in old_day["timeline"] if r["kind"] in {"attraction","meal"}}
            new_timeline={r["activity_id"]:r for r in new_day["timeline"] if r["kind"] in {"attraction","meal"}}
            prefix=[key for key,row in old_timeline.items() if datetime.fromisoformat(row["start_at"])<datetime.fromisoformat(cutoff)]
            check("remaining-day scope preserves executed and earlier activity content",all(new_activities.get(key)==old_activities[key] for key in prefix))
            check("remaining-day scope preserves executed and earlier activity times",all(key in new_timeline and all(new_timeline[key][field]==old_timeline[key][field] for field in ("start_at","end_at")) for key in prefix))
            check("remaining-day activities stay inside the authorized time range",all(key in new_timeline and datetime.fromisoformat(new_timeline[key]["start_at"])>=datetime.fromisoformat(cutoff) and datetime.fromisoformat(new_timeline[key]["end_at"])<=datetime.fromisoformat(end) for key in new_activities if key not in prefix))
            check("remaining-day scope actually replaces a future visit",remaining["activity_id"] not in new_activities)
            check("remaining-day scope preserves other dates exactly",proposal["plan"]["days"][:2]==before["plan"]["days"][:2])
            check("remaining-day scope preserves later confirmed booking",all(new_timeline[booked["activity_id"]][field]==old_timeline[booked["activity_id"]][field] for field in ("start_at","end_at")))
            confirm(conversation,proposal)
            check("remaining-day confirmation advances one version",history(identity)["version"]==before["version"]+1)
            report.update(passed=True,trip_id=identity,checks_passed=len(report["checks"]))
        except Exception as exc:
            report["error"]=f"{type(exc).__name__}: {exc}"
            raise
        finally:
            report["finished_at"]=datetime.now(timezone.utc).isoformat()
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",default="http://127.0.0.1:18080")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--cross-city-only",action="store_true",help="Only rerun changed cross-city/hotel generation checks")
    args=parser.parse_args()
    result=run(args.base_url,args.output,args.cross_city_only)
    print(json.dumps({"passed":result["passed"],"checks_passed":result["checks_passed"]}))
