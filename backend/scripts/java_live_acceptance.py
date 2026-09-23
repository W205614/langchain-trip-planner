"""Opt-in, resumable live acceptance through Java. Never resets the durable ledger.

Private test credentials stay outside Git. Original-account password login is a
separate user check; administrative review uses a locally signed existing-admin token.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time
from uuid import uuid4
import httpx
from dotenv import dotenv_values
from jose import jwt

ROOT=Path(__file__).resolve().parents[2]
LIMITS={"text":12,"vision":1,"embedding":50,"amap":100}

def budget_ledger():
    configured=dotenv_values(ROOT/"deploy/runtime/agent.env").get("ACCEPTANCE_BUDGET_FILE") or ""
    prefix="/app/runtime/acceptance-budget"
    if not (configured.startswith(prefix) and configured.endswith(".sqlite3") and
            "/" not in configured[len("/app/runtime/"):]):
        raise RuntimeError("Live acceptance requires a named persistent budget ledger")
    return ROOT/"backend/data/agent-runtime"/Path(configured).name,configured

def counts():
    ledger,_=budget_ledger()
    if not ledger.exists():return {kind:0 for kind in LIMITS}
    with sqlite3.connect("file:"+ledger.as_posix()+"?mode=ro",uri=True) as db:
        used=dict(db.execute("SELECT kind,used FROM calls"))
    assert all(used.get(k,0)<=limit for k,limit in LIMITS.items())
    return {k:used.get(k,0) for k in LIMITS}

def run(args):
    _,configured=budget_ledger()
    runtime=subprocess.check_output(["docker","compose","exec","-T","agent","python","-c",
        "import os; print(os.environ.get('ACCEPTANCE_BUDGET_FILE',''))"],cwd=ROOT,text=True).strip()
    if runtime!=configured:
        raise RuntimeError("Live acceptance requires the configured persistent budget in the running Agent")
    private=args.private.resolve()
    if private==ROOT or ROOT in private.parents:raise ValueError("Private state must be outside Git")
    private.mkdir(parents=True,exist_ok=True)
    state_path=private/"live-state.json"
    state=json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"cases":{}}
    report_path=ROOT/"docs/evidence/java-migration/live.json"
    def save():
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
        report={"mode":"live_java_entry","limits":LIMITS,"counts":counts(),"cases":state["cases"],
            "document":state.get("document_report"),"user_original_login":"separate account-holder check; see acceptance report",
            "counting":"durable pre-send reservations; failed attempts count; each category capped independently"}
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    with httpx.Client(base_url="http://127.0.0.1:8080",timeout=35) as client:
        assert client.get("/healthz").status_code==200
        if "token" not in state:
            state.setdefault("username","live_java_"+uuid4().hex[:8]);state.setdefault("password",secrets.token_urlsafe(24));save()
            response=client.post("/api/auth/login",json={k:state[k] for k in ("username","password")})
            if response.status_code==401:
                response=client.post("/api/auth/register",json={k:state[k] for k in ("username","password")})
            response.raise_for_status();state["token"]=response.json()["access_token"];save()
        client.headers["Authorization"]="Bearer "+state["token"]
        def wait_task(identity):
            start=time.monotonic()
            while time.monotonic()-start<330:
                response=client.get(f"/api/trip/tasks/{identity}");response.raise_for_status()
                task=response.json()["data"]
                if task["status"] in {"succeeded","needs_attention","failed","cancelled"}:return task
                time.sleep(2)
            raise RuntimeError("Task exceeded acceptance observation window; no resubmission")
        def plan_case(name,request,path="/api/trip/tasks",headers=None):
            if name in state["cases"]:return state["cases"][name]
            key=state.setdefault(name+"_key",str(uuid4()));save()
            identity=state.get(name+"_task")
            if not identity:
                response=client.post(path,json=request,headers={"Idempotency-Key":key,**(headers or {})})
                response.raise_for_status();identity=response.json()["data"]["id"]
                state[name+"_task"]=identity;save()
            task=wait_task(identity);result=task.get("result",{});quality=result.get("quality",{})
            ok=task["status"] in {"succeeded","needs_attention"} and result.get("saved") is True
            if task["status"]=="needs_attention":ok=ok and quality.get("outcome")=="draft"
            if result:
                detail=client.get(f"/api/history/{result['id']}");detail.raise_for_status()
                ok=ok and detail.json()["data"]["plan"]==result["data"]
            row={"task_id":identity,"status":task["status"],"error_code":task.get("error_code"),
                "contract_passed":bool(ok),"outcome":quality.get("outcome"),"data_gaps":quality.get("data_gaps",[]),
                "record_id":result.get("id"),"version":result.get("version"),"usage":task.get("usage",{}),"counts_after":counts()}
            state["cases"][name]=row;save();print(json.dumps({"case":name,**row},ensure_ascii=True),flush=True)
            return row
        try:
            if args.action=="plans":
                for name,city,days in (("beijing_one_day","北京",1),("shanghai_two_days","上海",2)):
                    start=date.today()+timedelta(days=1)
                    request={"city":city,"start_date":str(start),"end_date":str(start+timedelta(days=days-1)),
                        "travel_days":days,"transportation":"公共交通","accommodation":"经济型酒店",
                        "preferences":["文化历史"],"free_text_input":"节奏适中，使用可信景点，不确定的信息请标注。"}
                    plan_case(name,request)
                original=state["cases"].get("shanghai_two_days",{})
                if original.get("record_id"):
                    record=client.get(f"/api/history/{original['record_id']}").json()["data"]
                    plan_case("revise_one_day",{"day_index":0,"instruction":"第一天节奏放缓，保留真实景点，其他天不要变更。"},
                        f"/api/history/{original['record_id']}/revise-task",{"If-Match":str(record["version"])})
                if "public_research" not in state["cases"]:
                    response=client.post("/api/research",json={"city":"北京","query":"故宫参观注意事项与资料来源"})
                    body=response.json()
                    state["cases"]["public_research"]={"http_status":response.status_code,"response":body,"counts_after":counts()};save()
                    print(json.dumps({"public_research":body},ensure_ascii=True),flush=True)
            else:
                cfg=dotenv_values(ROOT/"deploy/runtime/business.env")
                snapshot=json.loads((args.backup/"business.json").read_text(encoding="utf-8"))
                admin=next(u for u in snapshot["users"] if u["is_admin"])
                token=jwt.encode({"sub":str(admin["id"]),"ver":admin["token_version"],"exp":datetime.now(timezone.utc)+timedelta(minutes=15)},cfg["JWT_SECRET_KEY"],algorithm="HS256")
                client.headers["Authorization"]="Bearer "+token
                if args.action=="extract" and "document_id" not in state:
                    from PIL import Image,ImageDraw,ImageFont
                    picture=Image.new("RGB",(1200,600),"white");draw=ImageDraw.Draw(picture)
                    font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",32)
                    draw.multiline_text((50,60),"JAVA MIGRATION ACCEPTANCE - SYNTHETIC DOCUMENT\n\nCity: Beijing\nTopic: travel preparation\nCheck official opening times before departure.\nDo not treat this test page as ticket or price information.",fill="black",font=font,spacing=20)
                    data=io.BytesIO();picture.save(data,format="PNG")
                    response=client.post("/api/knowledge/submissions",data={"city":"北京","title":"Java迁移验收合成资料（非景点事实）"},files={"file":("acceptance.png",data.getvalue(),"image/png")})
                    response.raise_for_status();state["document_id"]=response.json()["data"]["id"];save()
                identity=state["document_id"];prefix=f"/api/knowledge/admin/submissions/{identity}"
                document=client.get(prefix+"/preview").json()["data"]
                if args.action=="extract" and document["status"]=="pending":
                    response=client.post(prefix+"/approve",json={"source_tier":"reviewed","note":"合成验收资料，解析后人工核对，不自动发布"});response.raise_for_status()
                if args.action=="publish":
                    if document["status"]!="awaiting_review":raise RuntimeError("Must manually inspect extraction before publish")
                    response=client.post(prefix+"/publish",json={"version":document["version"]});response.raise_for_status()
                expected="awaiting_review" if args.action=="extract" else "published"
                for _ in range(150):
                    document=client.get(prefix+"/preview").json()["data"]
                    if document["status"] in {expected,"failed"}:break
                    time.sleep(2)
                state["document_report"]={"id":identity,"status":document["status"],"version":document["version"],
                    "phase":args.action,"counts_after":counts(),"synthetic_fixture":True}
                save();print(json.dumps(document,ensure_ascii=True),flush=True)
        finally:save()

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["plans","extract","publish"])
    parser.add_argument("--private",required=True,type=Path);parser.add_argument("--backup",required=True,type=Path)
    run(parser.parse_args())
