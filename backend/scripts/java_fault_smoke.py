"""Fixed isolated-stack cross-service failure checks; never use real providers."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import httpx


def run(output):
    compose=["docker","compose","-p","trip-validation","-f","docker-compose.validation.yml"]
    with httpx.Client(base_url="http://127.0.0.1:18080",timeout=30) as client:
        assert client.get("/api/validation/fixture").json()["offline_fixture"]
        account=client.post("/api/auth/register",json={"username":"fault_"+uuid4().hex[:10],"password":"fault-test123"});account.raise_for_status()
        client.headers["Authorization"]="Bearer "+account.json()["access_token"]
        body={"city":"北京","start_date":"2026-09-11","end_date":"2026-09-11","travel_days":1,"transportation":"步行","accommodation":"酒店","free_text_input":"fixture:slow"}
        def state(identity):
            response=client.get(f"/api/trip/tasks/{identity}");response.raise_for_status();return response.json()["data"]
        def wait(identity,statuses):
            for _ in range(150):
                result=state(identity)
                if result["status"] in statuses:return result
                time.sleep(.2)
            raise AssertionError(f"Task never reached {statuses}")
        def submit(key):
            response=client.post("/api/trip/tasks",json=body,headers={"Idempotency-Key":key});response.raise_for_status();return response.json()["data"]["id"]
        first_key=str(uuid4());first=submit(first_key);wait(first,{"running"});time.sleep(1)
        try:
            subprocess.run(compose+["stop","agent"],check=True,capture_output=True)
            failed=wait(first,{"failed"})
            assert failed["error_code"]=="AGENT_CONNECTION_LOST",failed
            assert submit(first_key)==first and state(first)["status"]=="failed"
        finally:
            subprocess.run(compose+["up","-d","--wait","agent"],check=True,capture_output=True)
        second=submit(str(uuid4()));wait(second,{"running"});time.sleep(1)
        cancelled=client.post(f"/api/trip/tasks/{second}/cancel");cancelled.raise_for_status()
        assert cancelled.json()["data"]["status"]=="cancelled"
        # Let the already-issued fixture model request finish, exercising late-result rejection.
        time.sleep(21)
        assert state(second)["status"]=="cancelled"
        assert client.get("/api/history").json()["total"]==0
        report={"mode":"isolated_java_agent_faults","agent_disconnect_error":failed["error_code"],
            "same_key_did_not_regenerate":True,"cancelled_late_result_rejected":True,"history_count":0,"paid_calls":0}
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding="utf-8");print(json.dumps(report))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args().output)
