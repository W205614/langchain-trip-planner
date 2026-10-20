"""Resumable live-provider sampling, tied to one existing durable acceptance run.

Never rotates run IDs or retries a failed task implicitly. Private credentials and
raw plans stay outside the repository; the public report contains reviewed facts.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote, urlparse, unquote
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from deployment_assets import inspect, sql

ROOT=Path(__file__).resolve().parents[2]
LIMITS={"text":12,"embedding":40,"amap":400}


def business_database(backend_env, postgres_env):
    # The business DB can differ from PostgreSQL's bootstrap POSTGRES_DB.
    jdbc=backend_env.get("JDBC_DATABASE_URL", "")
    if jdbc.startswith("jdbc:postgresql://"):
        database=unquote(urlparse(jdbc.removeprefix("jdbc:")).path.lstrip("/"))
        if database:
            return database
    return postgres_env.get("POSTGRES_DB", "trip")


def instant(value):
    parsed=datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Expected timezone-aware persisted timestamp")
    return parsed



def run(args):
    private=args.private_dir.resolve()
    if private.is_relative_to(ROOT):
        raise ValueError("Private live credentials must be outside the repository")
    if urlparse(args.base_url).hostname not in {"127.0.0.1","localhost"}:
        raise ValueError("Only the local deployment is supported")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}",args.run_id):
        raise ValueError("Invalid existing run ID")
    deployed=inspect("langchain-trip-planner-backend-1")
    env=dict(x.split("=",1) for x in deployed["Config"]["Env"] if "=" in x)
    if env.get("FINAL_ACCEPTANCE_RUN_ID")!=args.run_id:
        raise RuntimeError("Backend is not pinned to the requested acceptance run")
    postgres=inspect("langchain-trip-planner-postgres-1")
    pg_env=dict(x.split("=",1) for x in postgres["Config"]["Env"] if "=" in x)
    database=business_database(env,pg_env)
    def counts():
        rows=sql(postgres["Id"],database,f"SELECT kind||':'||used FROM outbound_budget_counters WHERE scope='acceptance:{args.run_id}'",pg_env.get("POSTGRES_USER","trip"))
        result={k:0 for k in LIMITS}
        result.update({kind:int(used) for kind,used in (row.split(":") for row in rows.splitlines())})
        return result
    private.mkdir(parents=True,exist_ok=True)
    path=private/"state.json"
    state=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"run_id":args.run_id,"username":"final_live_"+uuid4().hex[:9],"password":secrets.token_urlsafe(24)}
    if state["run_id"]!=args.run_id:
        raise RuntimeError("Refusing a different run for existing private state")
    report={"mode":"live_providers_http","run_id":args.run_id,"stage":args.stage,"passed":False,
            "started_at":datetime.now(timezone.utc).isoformat(),"limits":LIMITS,"before":counts(),"checks":[],
            "boundary":"Independent live-provider sample; cross-city transport is a hypothetical user-confirmed input, not a real booking. No SLA or generalization claim."}
    def persist():path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    def check(name,condition):
        if not condition:raise AssertionError(name)
        report["checks"].append(name)
    with httpx.Client(base_url=args.base_url,timeout=45,trust_env=False) as c:
        def req(method,url,expected=(200,),**kwargs):
            r=c.request(method,url,**kwargs)
            if r.status_code not in expected:raise AssertionError(f"{url}: HTTP {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        def history(identity):return req("GET",f"/api/history/{identity}")["data"]
        def wait(identity):
            until=time.monotonic()+300
            while time.monotonic()<until:
                task=req("GET",f"/api/trip/tasks/{identity}")["data"]
                if task["status"] in {"succeeded","needs_attention","failed","cancelled"}:
                    report["task"]={"id":identity,"status":task["status"],"error_code":task.get("error_code")}
                    check("task produces persisted usable result",task["status"] in {"succeeded","needs_attention"} and bool(task.get("result",{}).get("id")))
                    return history(task["result"]["id"])
                time.sleep(1)
            raise AssertionError("Task exceeded bounded live wait; ID retained for inspection")
        try:
            check("existing acceptance counters respect fixed caps",all(report["before"][kind]<=limit for kind,limit in LIMITS.items()))
            check("no fixture route",c.get("/api/validation/fixture").status_code==404)
            persist()
            auth=c.post("/api/auth/login",json={k:state[k] for k in ("username","password")})
            if auth.status_code==401:auth=c.post("/api/auth/register",json={k:state[k] for k in ("username","password")})
            auth.raise_for_status();c.headers["Authorization"]="Bearer "+auth.json()["access_token"]
            start=datetime.now(ZoneInfo("Asia/Shanghai")).date()+timedelta(days=10)
            if args.stage in {"plan","crosscity"}:
                stage=args.stage
                if stage+"_task" not in state:
                    if stage+"_request" not in state:
                        current=counts()
                        check("budget remains before generation",current["text"]<=LIMITS["text"]-2 and current["amap"]<=LIMITS["amap"]-40)
                        planning={"timezone":"Asia/Shanghai","day_start":"09:00","day_end":"20:00","budget_mode":"estimate","budget_cents":200000}
                        if stage=="crosscity":
                            planning.update(day_windows=[{"day_index":1,"start":"07:00","end":"20:00"}],
                                city_segments=[{"city":"北京","start_day":0,"end_day":0},{"city":"上海","start_day":1,"end_day":1}],
                                commitments=[{"id":"sample-train","activity_id":"commitment:sample-train","day_index":1,"category":"transport","status":"confirmed","title":"用户输入的示例跨城交通（非真实票务）","time_mode":"fixed","origin_city":"北京","destination_city":"上海","origin_location":{"longitude":116.3785,"latitude":39.8652},"destination_location":{"longitude":121.3271,"latitude":31.2009},"start_at":f"{start+timedelta(days=1)}T08:00:00+08:00","end_at":f"{start+timedelta(days=1)}T12:30:00+08:00","amount_cents":55000}])
                        request={"city":"北京","start_date":str(start),"end_date":str(start+timedelta(days=1)),"travel_days":2,"transportation":"公共交通","accommodation":"经济型酒店","preferences":["历史文化"],"planning_input":planning,"free_text_input":"每天选择三个可信景点；资料不足明确标注，不要编造营业时间或票价。"}
                        state[stage+"_request"]=request;state.setdefault(stage+"_key",str(uuid4()));persist()
                    # Replay exactly the durable snapshot/key after a lost HTTP response.
                    submitted=req("POST","/api/trip/tasks",(202,),json=state[stage+"_request"],headers={"Idempotency-Key":state[stage+"_key"]})
                    state[stage+"_task"]=submitted["data"]["id"];persist()
                record=wait(state[stage+"_task"]);state[stage+"_trip"]=record["id"];persist()
                check("two days persisted",len(record["plan"]["days"])==2)
                check("no blocking draft",record["quality"].get("outcome") in {"complete","degraded"})
                check("each day has stable trusted POIs",all(d["attractions"] and all(a.get("poi_id") and a.get("activity_id") for a in d["attractions"]) for d in record["plan"]["days"]))
                check("timeline emitted",all(d.get("timeline") for d in record["plan"]["days"]))
                if stage=="crosscity":
                    check("two city segments retained",[d.get("city") for d in record["plan"]["days"]]==["北京","上海"])
                    rows=[r for r in record["plan"]["days"][1]["timeline"] if r["kind"]=="attraction"]
                    arrival=datetime.fromisoformat(state[stage+"_request"]["planning_input"]["commitments"][0]["end_at"])
                    check("Shanghai visits follow confirmed arrival",bool(rows) and all(datetime.fromisoformat(r["start_at"])>=arrival for r in rows))
                    persisted=req("GET",f"/api/trips/{record['id']}/planning-input")["data"]["planning_input"]["commitments"]
                    train=next(item for item in persisted if item.get("activity_id")=="commitment:sample-train")
                    check("cross-city booking keeps confirmed integer cents",train["status"]=="confirmed" and train["amount_cents"]==55000 and train["version"]>=1)
                    check("cross-city quality counts confirmed booking cost",record["quality"].get("cost_summary",{}).get("confirmed_cents")==55000)
                (private/(stage+"-plan.json")).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
                report.update(record_id=record["id"],quality=record["quality"],days=[{"city":d.get("city"),"names":[a["name"] for a in d["attractions"]],"feasibility":d.get("feasibility"),"timeline":d.get("timeline")} for d in record["plan"]["days"]])
            elif args.stage=="proposal":
                identity=state["plan_trip"]
                if "proposal_task" not in state:
                    if "proposal_request" not in state:
                        if "proposal_setup" not in state:
                            initial=state.get("proposal_before") or history(identity)
                            selected=state.get("proposal_locked") or initial["plan"]["days"][1]["attractions"][0]
                            slot=state.get("proposal_slot") or next(r for r in initial["plan"]["days"][1]["timeline"] if r["activity_id"]==selected["activity_id"])
                            state["proposal_setup"]={"before":initial,"selected":selected,"slot":slot}
                            persist()
                        setup=state["proposal_setup"];selected=setup["selected"];slot=setup["slot"]
                        cfg=req("GET",f"/api/trips/{identity}/planning-input")["data"]
                        check("setup still targets original trip version",cfg["trip_version"]==setup["before"]["version"])
                        # Recover a lost lock response by inspecting the authoritative input.
                        if not any(lock.get("activity_id")==selected["activity_id"] for lock in cfg["planning_input"]["locks"]):
                            activity_path=f"/api/trips/{identity}/activities/{quote(selected['activity_id'],safe='')}/state"
                            req("POST",activity_path,json={"planning_revision":cfg["planning_revision"],"locked":True},headers={"If-Match":str(cfg["trip_version"])})
                        if "proposal_commitment_request" not in state:
                            state["proposal_commitment_request"]={"day_index":1,"category":"ticket","status":"confirmed","title":"用户确认的抽样预约（非实际出票）","amount":"0.00","activity_id":selected["activity_id"],"poi_id":selected["poi_id"],"location":selected["location"],"time_mode":"fixed","start_at":slot["start_at"],"end_at":slot["end_at"]}
                            state.setdefault("proposal_commitment_key",str(uuid4()));persist()
                        if "proposal_commitment_id" not in state:
                            saved=req("POST",f"/api/trips/{identity}/commitments",json=state["proposal_commitment_request"],headers={"Idempotency-Key":state["proposal_commitment_key"]})
                            state["proposal_commitment_id"]=saved["id"];persist()
                        ledger=req("GET",f"/api/trips/{identity}/commitments")["data"]
                        booked=next(row for row in ledger if row["id"]==state["proposal_commitment_id"])
                        check("ledger stores confirmed amount in integer cents",booked["status"]=="confirmed" and booked["amount_cents"]==0 and booked["version"]>=1)
                        cfg=req("GET",f"/api/trips/{identity}/planning-input")["data"]
                        state["proposal_before"]=history(identity);state["proposal_locked"]=selected;state["proposal_slot"]=slot
                        if "conversation" not in state:
                            state["conversation"]=req("POST","/api/assistant/conversations",json={"active_trip_id":identity})["id"];persist()
                        state.setdefault("proposal_key",str(uuid4()))
                        state["proposal_input_revision"]=cfg["planning_revision"]
                        state["proposal_request"]={"content":"只调整第2天，保留确认预约和锁定活动，尽量少走路，其他日期完全不变","mode":"revise","record_id":identity,"version":state["proposal_before"]["version"],"day_index":1,"planning_revision":cfg["planning_revision"],"revision_scope":{"day_indices":[1]}}
                        persist()
                        check("budget remains before revision",counts()["text"]<12 and counts()["amap"]<360)
                    submitted=req("POST",f"/api/assistant/conversations/{state['conversation']}/messages",(202,),json=state["proposal_request"],headers={"Idempotency-Key":state["proposal_key"]})
                    state["proposal_task"]=submitted["task_id"];persist()
                proposal=wait(state["proposal_task"]);before=state["proposal_before"]
                check("revision has usable validated outcome",proposal["quality"].get("validated_outcome") in {"complete","degraded"})
                status=proposal["quality"].get("assistant_proposal_status")
                check("stored record is a proposal in known state",status in {"pending","confirmed"})
                state.setdefault("proposal_preview",proposal["plan"]);state.setdefault("proposal_id",proposal["id"]);state.setdefault("proposal_version",proposal["version"]);persist()
                preview=state["proposal_preview"]
                url=f"/api/assistant/conversations/{state['conversation']}/proposals/{proposal['id']}/confirm"
                if status=="pending":
                    dependency=proposal["quality"].get("revision_parent",{})
                    check("proposal binds trip and planning input revisions",dependency.get("record_id")==identity and dependency.get("version")==before["version"] and dependency.get("planning_revision")==state["proposal_input_revision"])
                    original=history(identity)
                    check("preview leaves original intact",original["version"]==before["version"] and original["plan"]==before["plan"])
                    check("first day unchanged",preview["days"][0]==before["plan"]["days"][0])
                    selected=state["proposal_locked"]
                    check("locked activity unchanged",selected in preview["days"][1]["attractions"])
                    slot=next(r for r in preview["days"][1]["timeline"] if r["activity_id"]==selected["activity_id"])
                    check("confirmed appointment unchanged",all(instant(slot[k])==instant(state["proposal_slot"][k]) for k in ("start_at","end_at")))
                    confirmed=req("POST",url,(200,409),json={},headers={"If-Match":str(state["proposal_version"])})
                    if confirmed.get("success") is not True:
                        latest=history(proposal["id"])
                        check("confirmation conflict is an already applied same proposal",latest["quality"].get("assistant_proposal_status")=="confirmed" and latest["quality"].get("applied_to")==identity)
                else:
                    check("recovered confirmed proposal belongs to target",proposal["quality"].get("applied_to")==identity)
                req("POST",url,(409,),json={},headers={"If-Match":str(state["proposal_version"])})
                after=history(identity)
                check("confirmation stores exact preview once",after["version"]==before["version"]+1 and after["plan"]==preview)
                state["confirmed"]=True;persist()
                report.update(record_id=identity,proposal_id=proposal["id"],quality=proposal["quality"])
            elif args.stage=="research":
                if "research" not in state:
                    if state.get("research_attempted"):
                        raise RuntimeError("Research response is unknown after an earlier send; inspect that attempt rather than implicitly retrying a paid request")
                    check("budget remains before research",counts()["text"]<12 and counts()["embedding"]<40)
                    state["research_attempted"]=True;persist()
                    state["research"]=req("POST","/api/research",json={"city":"北京","query":"故宫参观前有哪些需要核实的信息？请区分攻略与已确认事实。"});persist()
                data=state["research"].get("data",{})
                check("research has nonempty answer",isinstance(data.get("answer"),str) and bool(data["answer"].strip()))
                check("research classifies evidence availability",data.get("answer_status") in {"grounded_answer","extractive_fallback","insufficient_evidence"})
                check("research exposes evidence and sources",isinstance(data.get("sources"),list) and isinstance(data.get("evidence"),list))
                if data["answer_status"]!="insufficient_evidence":
                    check("grounded result has supporting sources",bool(data["sources"]) and bool(data["evidence"]))
                report["research"]=state["research"]
            else:
                for stage in ("plan","crosscity"):
                    record=history(state[stage+"_trip"])
                    check(stage+" remains readable after rebuild",bool(record["plan"]["days"]))
                    snapshot=json.loads((private/(stage+"-plan.json")).read_text(encoding="utf-8"))
                    expected=state["proposal_preview"] if stage=="plan" else snapshot["plan"]
                    expected_version=state["proposal_before"]["version"]+1 if stage=="plan" else snapshot["version"]
                    check(stage+" exact persisted plan survives rebuild",record["plan"]==expected and record["version"]==expected_version)
                check("confirmed proposal persists",state.get("confirmed") is True)
            report["passed"]=True
        except Exception as exc:
            report["error"]=f"{type(exc).__name__}: {exc}"[:1200]
            raise
        finally:
            report.update(after=counts(),finished_at=datetime.now(timezone.utc).isoformat())
            if any(report["after"][kind]>limit for kind,limit in LIMITS.items()):
                report["passed"]=False;report["budget_error"]="Durable acceptance counter exceeded fixed cap"

            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if not report["passed"]:
        raise RuntimeError(report.get("budget_error", "Live acceptance failed"))
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url",default="http://127.0.0.1:8380")
    p.add_argument("--run-id",required=True)
    p.add_argument("--private-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--stage",choices=("plan","proposal","crosscity","research","readback"),required=True)
    result=run(p.parse_args())
    print(json.dumps({"stage":result["stage"],"passed":result["passed"],"counts":result["after"]}))
