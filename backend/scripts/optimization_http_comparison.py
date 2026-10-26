"""Frozen development counterexamples against real Java/PG/Agent HTTP, offline providers.

Stop only the explicitly named fixture container to reproduce a transient outage.
No business API interception, database fabrication, daily containers or paid sends.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import httpx

INPUT={"city":"北京","start_date":"2026-11-01","end_date":"2026-11-02","travel_days":2,
       "transportation":"公共交通","accommodation":"经济型酒店","planning_input":{},"preferences":["历史文化"]}

def run(base_url,fixture,variant,output,outage_only=False):
    info=json.loads(subprocess.check_output(["docker","inspect",fixture],text=True))[0]
    project=info["Config"].get("Labels",{}).get("com.docker.compose.project","")
    if project not in {"trip-opt-baseline-20261026","trip-opt-candidate-20261026","trip-opt-candidate-20261026-v2"} or info["Config"].get("Labels",{}).get("com.docker.compose.service")!="amap-fixture":
        raise RuntimeError("Refusing fault injection outside this optimization fixture project")
    report={"variant":variant,"dataset_role":"frozen_development_counterexamples","input":INPUT,
            "input_sha256":hashlib.sha256(json.dumps(INPUT,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            "providers":"explicit offline substitutes","real_paid_calls":0,"cases":[],
            "not_proven":["general accuracy","independent human review","live-provider quality","capacity","HA"],
            "started_at":datetime.now(timezone.utc).isoformat()}
    def checkpoint():
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    checkpoint()
    with httpx.Client(base_url=base_url,timeout=40,trust_env=False) as client:
        response=client.get("/api/validation/fixture");response.raise_for_status()
        if response.json().get("offline_fixture") is not True:raise RuntimeError("Offline fixture marker required")
        owner=client.post("/api/auth/register",json={"username":"comparison_"+uuid4().hex[:16],"password":"Isolated!Comparison_2026"})
        owner.raise_for_status();client.headers["Authorization"]="Bearer "+owner.json()["access_token"]
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()
        def create():
            identity=request("POST","/api/trip/tasks",json=INPUT,headers={"Idempotency-Key":str(uuid4())})["data"]["id"]
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                state=request("GET",f"/api/trip/tasks/{identity}")["data"]
                if state["status"] in {"succeeded","needs_attention"}:return state["result"]
                if state["status"] in {"failed","cancelled"}:raise RuntimeError("Fixture generation failed: "+state.get("error_code",""))
                time.sleep(.25)
            raise TimeoutError("Fixture task did not complete")
        # A: a provider outage is distinct from a nonexistent place.
        trip=create();identity=trip["id"];before=request("GET",f"/api/history/{identity}")["data"]
        before_ids=[a["poi_id"] for d in before["plan"]["days"] for a in d["attractions"]]
        subprocess.run(["docker","stop",fixture],capture_output=True,check=True)
        try:
            checked=client.post(f"/api/trips/{identity}/verify",headers={"If-Match":str(before["version"])})
            checked.raise_for_status()
            status=checked.status_code
        finally:subprocess.run(["docker","start",fixture],capture_output=True,check=True)
        after=request("GET",f"/api/history/{identity}")["data"]
        after_ids=[a["poi_id"] for d in after["plan"]["days"] for a in d["attractions"]]
        report["cases"].append({"id":"provider-outage-keeps-places","http_status":status,"before_poi_ids":before_ids,"after_poi_ids":after_ids,"preserved":before_ids==after_ids})
        checkpoint()
        if outage_only:
            report["completed_at"]=datetime.now(timezone.utc).isoformat();checkpoint()
            return {"variant":variant,"cases":1,"output":str(output),"real_paid_calls":0}
        # B: changing conditions must not authorize a lock on an old computed time.
        trip=create();identity=trip["id"]
        planning=request("GET",f"/api/trips/{identity}/planning-input")["data"]
        activity=next(a for d in planning["plan"]["days"] for a in d["attractions"])
        planning["planning_input"]["day_end"]="16:00"
        saved=request("PUT",f"/api/trips/{identity}/planning-input",headers={"If-Match":str(planning["trip_version"])},json={"planning_revision":planning["planning_revision"],"planning_input":planning["planning_input"]})["data"]
        locked=client.post(f"/api/trips/{identity}/activities/{activity['activity_id']}/state",headers={"If-Match":str(saved["trip_version"])},json={"planning_revision":saved["planning_revision"],"locked":True,"lock_mode":"time"})
        report["cases"].append({"id":"changed-input-rejects-old-time-lock","http_status":locked.status_code,"error_code":locked.json().get("error_code"),"rejected_as_stale":locked.status_code==409 and locked.json().get("error_code")=="SCHEDULE_STALE"})
        checkpoint()
        # C: the short reply must complete the original adjustment request.
        trip=create();identity=trip["id"]
        conversation=request("POST","/api/assistant/conversations",json={"active_trip_id":identity,"title":"frozen clarification"})["id"]
        first=request("POST",f"/api/assistant/conversations/{conversation}/messages",json={"mode":"auto","content":"轻松一点"},headers={"Idempotency-Key":str(uuid4())})
        second=client.post(f"/api/assistant/conversations/{conversation}/messages",json={"mode":"auto","content":"第2天"},headers={"Idempotency-Key":str(uuid4())})
        result=second.json()
        report["cases"].append({"id":"clarification-retains-original-intent","first_status":first.get("status"),"second_http_status":second.status_code,"created_revision_task":bool(result.get("task_id")),"reported_intent":result.get("intent")})
        checkpoint()
    report["completed_at"]=datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"variant":variant,"cases":len(report["cases"]),"output":str(output),"real_paid_calls":0}

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--base-url",required=True);p.add_argument("--fixture-container",required=True);p.add_argument("--variant",choices=("baseline","candidate"),required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--outage-only",action="store_true")
    a=p.parse_args()
    try:print(json.dumps(run(a.base_url,a.fixture_container,a.variant,a.output,a.outage_only)))
    except Exception as ex:
        if a.output.exists():
            partial=json.loads(a.output.read_text(encoding="utf-8"));partial.update(failed=True,error_type=type(ex).__name__)
            a.output.write_text(json.dumps(partial,ensure_ascii=False,indent=2),encoding="utf-8")
        raise
