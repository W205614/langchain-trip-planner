"""One synthetic daily Alertmanager->persistent receiver firing/resolved check.

Default does nothing. --execute uses only read-only Docker inspect/exec, local
Prometheus GET, and five synthetic internal alert POSTs. No lifecycle or business
API, provider request, configuration change, ordinary alert mutation or deletion.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
from uuid import uuid4
import yaml

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
PROJECT = "langchain-trip-planner"
RECEIVER_IMAGE = "sha256:397944d2c0a9333a1ce1fc3d42345bd796e971ebd1780e42917b30f5cc0e8517"
SHARED = Path(r"C:\Users\25452\.codex\visualizations\2026\10\25\01a172e2-8a39-7783-b536-b6dbe2f478cd\execution-20261026")

CHILD = r'''
import datetime,hashlib,json,os,pathlib,sys,time,urllib.request
arguments=json.load(sys.stdin)
name,job=arguments['name'],arguments['job']
inbox=pathlib.Path(os.environ.get('ALERT_INBOX','/inbox/alerts.jsonl'))
assert inbox.is_absolute() and inbox.is_relative_to(pathlib.Path('/inbox'))
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args):raise RuntimeError('redirect_forbidden')
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def http(url,body=None):
 data=None if body is None else json.dumps(body).encode()
 request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'} if data else {},method='POST' if data else 'GET')
 with client.open(request,timeout=10) as response:
  assert response.status==200
  raw=response.read(65537);assert len(raw)<=65536
  return json.loads(raw) if raw else None
def rows():
 result=[]
 if inbox.exists():
  assert inbox.stat().st_size<=2*1024*1024
  for line in inbox.read_text(encoding='utf-8').splitlines():
   item=json.loads(line)
   if item.get('name')==name and item.get('job')==job:
    assert set(item)=={'name','job','status','starts_at','event_id','received_at'}
    result.append({'row':item,'actual_jsonl_line':line})
 return result
def emit(phase,**values):print(json.dumps({'phase':phase,**values}),flush=True)
def wait(status,limit):
 deadline=time.monotonic()+limit
 while time.monotonic()<deadline:
  found=rows()
  if any(x['row']['status']==status for x in found):return found
  time.sleep(2)
 raise RuntimeError('timeout_waiting_for_'+status)
def duplicate(event):
 item=event['row']
 payload={'version':'4','status':item['status'],'receiver':'local-durable-inbox','alerts':[{'status':item['status'],'labels':{'alertname':name,'job':job},'startsAt':item['starts_at']}]}
 answer=http('http://127.0.0.1:9088/alerts',payload)
 assert answer.get('accepted')==0
 return answer
def stamp(value):return value.isoformat(timespec='seconds').replace('+00:00','Z')
now=datetime.datetime.now(datetime.timezone.utc)
alert={'labels':{'alertname':name,'job':job,'severity':'info','scope':'local-synthetic-delivery'},'annotations':{'summary':'Synthetic local delivery verification only; not a business incident.'},'startsAt':stamp(now-datetime.timedelta(seconds=10)),'endsAt':stamp(now+datetime.timedelta(minutes=10))}
sent=False;resolved=False
try:
 assert not rows()
 http('http://alertmanager:9093/api/v2/alerts',[alert]);sent=True
 http('http://alertmanager:9093/api/v2/alerts',[alert])
 emit('synthetic_firing_and_identical_refresh_posted')
 firing=wait('firing',45)
 assert len(firing)==1 and firing[0]['row']['status']=='firing'
 first_duplicate=duplicate(firing[0]);assert len(rows())==1
 emit('firing_persisted_receiver_semantic_duplicate_accepted_zero',events=firing,duplicate=first_duplicate)
 alert['endsAt']=stamp(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=1))
 http('http://alertmanager:9093/api/v2/alerts',[alert]);resolved=True
 emit('synthetic_resolution_posted_original_group_interval_retained')
 result=wait('resolved',165)
 assert len(result)==2 and sorted(x['row']['status'] for x in result)==['firing','resolved']
 assert len({x['row']['event_id'] for x in result})==2
 second_duplicate=duplicate(next(x for x in result if x['row']['status']=='resolved'))
 assert len(rows())==2
 for value in result:
  item=value['row'];basis={key:item[key] for key in ('name','job','status','starts_at')}
  assert hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest()==item['event_id']
 emit('complete',passed=True,events=result,firing_duplicate_accepted=0,resolved_duplicate_accepted=second_duplicate['accepted'],inbox_path=str(inbox),synthetic_post_count=5)
except BaseException as error:
 emit('failed',passed=False,error_type=type(error).__name__,reason=str(error) if isinstance(error,RuntimeError) else 'synthetic_protocol_assertion_or_transport_failure',events=rows())
finally:
 if sent and not resolved:
  try:
   alert['endsAt']=stamp(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=1))
   http('http://alertmanager:9093/api/v2/alerts',[alert])
   emit('failure_finally_resolved_only_the_owned_synthetic_alert')
  except BaseException as error:emit('failure_resolution_error',error_type=type(error).__name__)
'''


def require(value, message):
    if not value:
        raise RuntimeError(message)


def docker(*args, data=None):
    require(args[0] in {"ps", "exec"} or args[:2] == ("container", "inspect"), "unexpected_docker_command")
    result = subprocess.run(["docker", *args], input=data, capture_output=True, timeout=60)
    require(result.returncode == 0, "docker_read_or_exec_probe_failed")
    return result.stdout


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect_forbidden")


def get_prom(path):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with client.open("http://127.0.0.1:19090" + path, timeout=10) as response:
        require(response.status == 200, "daily_prometheus_not_ready")
        value = response.read(2 * 1024 * 1024 + 1)
        require(len(value) <= 2 * 1024 * 1024, "prometheus_response_too_large")
        return value


def run(output):
    require(not output.exists(), "fresh_output_required")
    events_path = output.with_name(output.stem + "-events.jsonl")
    require(not events_path.exists(), "fresh_event_export_required")
    identity = uuid4().hex[:12]
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "project": PROJECT, "synthetic_alertname": "TripSyntheticDelivery" + identity,
              "synthetic_job": "trip-local-synthetic-" + identity, "events": [], "checks": [],
              "docker_lifecycle_operations": 0, "direct_supplier_calls": 0,
              "boundary": ["Prometheus readiness/discovery/rule loading is checked; the test alert is injected into Alertmanager API, not through a new Prometheus rule.",
                           "Real Alertmanager v2 to durable receiver webhook firing/resolved; one unique synthetic group, original timing config unchanged.",
                           "Receiver duplicate checks directly replay equivalent synthetic event identity fields, not a captured byte-identical full Alertmanager body.",
                           "Dedup granularity is name/job/status/startsAt; no claim for cross-instance distinctions, all batch/concurrent cases or external human notifications."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    with events_path.open("x", encoding="utf-8"):
        pass
    exported = set()

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        window = json.loads((SHARED / "deployment-window.json").read_text(encoding="utf-8"))
        require(window.get("owner") == "Trip" and window.get("state") == "granted", "Trip_window_required")
        ids = docker("ps", "-aq", "--no-trunc", "--filter", "label=com.docker.compose.project=" + PROJECT).decode().split()
        found = json.loads(docker("container", "inspect", *ids))
        selected = {}
        for item in found:
            role = (item["Config"].get("Labels") or {}).get("com.docker.compose.service")
            if role in {"prometheus", "alertmanager", "alert-receiver"}:
                require(role not in selected, "ambiguous_daily_monitor_service")
                selected[role] = item
        require(set(selected) == {"prometheus", "alertmanager", "alert-receiver"}, "daily_monitor_services_missing")
        require(all(item["State"]["Running"] for item in selected.values()), "daily_monitor_not_running")
        receiver = selected["alert-receiver"]
        require(receiver["Image"] == RECEIVER_IMAGE, "receiver_exact_dedicated_image_mismatch")
        binding = selected["prometheus"]["NetworkSettings"]["Ports"]["9090/tcp"]
        require(any(x["HostIp"] == "127.0.0.1" and x["HostPort"] == "19090" for x in binding), "daily_prometheus_binding_mismatch")
        mount = next(x for x in receiver["Mounts"] if x["Destination"] == "/inbox")
        require(mount["Type"] == "volume" and mount["RW"], "receiver_persistent_inbox_not_mounted")
        report["services"] = {role: {"id": item["Id"], "image_id": item["Image"],
                                    "health": (item["State"].get("Health") or {}).get("Status")}
                              for role, item in selected.items()}
        report["persistent_inbox_mount"] = {key: mount[key] for key in ("Type", "Name", "Source", "Destination", "RW")}
        require(get_prom("/-/ready"), "empty_prometheus_readiness")
        managers = json.loads(get_prom("/api/v1/alertmanagers"))
        active = managers["data"]["activeAlertmanagers"]
        require(any(item.get("url") == "http://alertmanager:9093/api/v2/alerts" for item in active), "prometheus_does_not_discover_daily_alertmanager")
        rules = json.loads(get_prom("/api/v1/rules"))
        report["prometheus"] = {"url": "http://127.0.0.1:19090", "ready": True,
                                "active_local_alertmanager_discovered": True,
                                "loaded_rule_groups": [group["name"] for group in rules["data"]["groups"]]}
        cmd = (receiver["Config"].get("Entrypoint") or []) + (receiver["Config"].get("Cmd") or [])
        script = next(word for word in cmd if word.endswith(".py"))
        preflight = "import hashlib,json,pathlib,urllib.request; p=pathlib.Path(" + repr(script) + "); o=urllib.request.build_opener(urllib.request.ProxyHandler({})); s=json.loads(o.open('http://alertmanager:9093/api/v2/status',timeout=10).read()); print(json.dumps({'config':s['config']['original'],'receiver_code_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
        details = json.loads(docker("exec", receiver["Id"], "python", "-c", preflight))
        config = yaml.safe_load(details["config"])
        mounted_bytes = docker("exec", selected["alertmanager"]["Id"], "cat", "/etc/alertmanager/alertmanager.yml")
        expected_bytes = (ROOT / "deploy/alertmanager.yml").read_bytes()
        require(hashlib.sha256(mounted_bytes).digest() == hashlib.sha256(expected_bytes).digest(), "actual_alertmanager_mounted_config_differs")
        mounted_config = yaml.safe_load(mounted_bytes)
        mounted_destinations = mounted_config.get("receivers", [])
        require(len(mounted_destinations) == 1 and mounted_destinations[0]["name"] == "local-durable-inbox" and
                len(mounted_destinations[0].get("webhook_configs", [])) == 1 and
                mounted_destinations[0]["webhook_configs"][0].get("url") == "http://alert-receiver:9088/alerts",
                "mounted_config_has_unexpected_receiver")
        destinations = config.get("receivers", [])
        require(len(destinations) == 1 and destinations[0]["name"] == "local-durable-inbox", "nonlocal_receiver_configuration")
        webhooks = destinations[0].get("webhook_configs", [])
        # Alertmanager redacts webhook URL secrets even for this plain local
        # URL. Bind exact mounted config bytes and retain runtime redaction.
        require(len(webhooks) == 1 and webhooks[0]["url"] in {"http://alert-receiver:9088/alerts", "<secret>"} and webhooks[0].get("send_resolved") is True,
                "unexpected_webhook_protocol_or_resolved_disabled")
        require(not config["route"].get("routes") and config["route"]["receiver"] == "local-durable-inbox", "unexpected_child_routes")
        require(all(config["route"].get(key) == mounted_config["route"].get(key)
                    for key in ("receiver", "group_by", "group_wait", "group_interval", "repeat_interval")), "loaded_route_differs_from_mounted_route")
        require(not any(key.endswith("_configs") and key != "webhook_configs" and value for key, value in destinations[0].items()), "external_notification_configuration_present")
        expected = hashlib.sha256((ROOT / "backend/scripts/operational_alert_receiver.py").read_bytes()).hexdigest()
        require(details["receiver_code_sha256"] == expected, "receiver_runtime_source_hash_mismatch")
        report["protocol"] = {"receiver_code_sha256": expected, "alertmanager_config_sha256": hashlib.sha256(details["config"].encode()).hexdigest(),
                              "actual_mounted_config_sha256": hashlib.sha256(mounted_bytes).hexdigest(),
                              "runtime_webhook_url_redacted": webhooks[0]["url"] == "<secret>",
                              "webhook": "http://alert-receiver:9088/alerts", "send_resolved": True,
                              "group_wait": config["route"]["group_wait"], "group_interval": config["route"]["group_interval"],
                              "repeat_interval": config["route"]["repeat_interval"]}
        first = EVIDENCE / "daily-alert-delivery-result.json"
        if output != first and first.is_file():
            report["earlier_diagnostic"] = {"report": first.name, "boundary": "Pre-injection harness rejected Alertmanager's redacted URL; original failure retained, zero synthetic POSTs made."}
        report["stage"] = "bounded_synthetic_delivery_in_progress"; save()
        process = subprocess.Popen(["docker", "exec", "-i", receiver["Id"], "python", "-c", CHILD],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdin.write(json.dumps({"name": report["synthetic_alertname"], "job": report["synthetic_job"]}).encode())
        process.stdin.close()
        for line in process.stdout:
            item = json.loads(line)
            report["checks"].append({key: value for key, value in item.items() if key != "events"})
            for value in item.get("events", []):
                if value["row"]["event_id"] not in exported:
                    with events_path.open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(value["actual_jsonl_line"] + "\n")
                    exported.add(value["row"]["event_id"]); report["events"].append(value["row"])
            if item["phase"] == "complete":
                report["passed"] = item.get("passed") is True
            save(); print(json.dumps({"phase": item["phase"], "persisted_synthetic_events": len(exported)}), flush=True)
        require(process.wait(timeout=10) == 0 and report["passed"], "synthetic_chain_did_not_complete")
        require(len(report["events"]) == 2 and {item["status"] for item in report["events"]} == {"firing", "resolved"}, "missing_unique_persistent_states")
        report["jsonl_export"] = str(events_path)
        report["jsonl_export_sha256"] = hashlib.sha256(events_path.read_bytes()).hexdigest()
        report["stage"] = "complete"
    except BaseException as error:
        report["passed"] = False
        report["error"] = {"type": type(error).__name__, "reason": str(error) if isinstance(error, RuntimeError) else "bounded_protocol_or_transport_check_failed"}
    report["finished_at"] = datetime.now(timezone.utc).isoformat(); save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, default=EVIDENCE / "daily-alert-delivery-result.json")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"execute": False, "project": PROJECT, "receiver_image": RECEIVER_IMAGE,
                          "synthetic_posts": 5, "expected_wait_seconds": 135, "lifecycle_operations": 0})); return 0
    report = run(args.output)
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "events": len(report["events"]), "lifecycle_operations": 0}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
