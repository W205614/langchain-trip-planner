"""Maintain the user-authorized optimization scope without claiming unverified completion."""
from __future__ import annotations
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/optimization-20261026"
COORD = Path(r"C:\Users\25452\.codex\visualizations\2026\10\25\01a172e2-8a39-7783-b536-b6dbe2f478cd\execution-20261026")
SCOPE = {
    0: ("baseline and common delivery requirements", ["scope ledger", "frozen evaluation and blind review", "persistent paid-call limits", "targeted tests and real-role browser", "deployment window", "backup and safe cleanup", "README and exact-SHA CI", "complete guide handoff"]),
    1: ("lossless reverification", ["timeout/rate-limit/connection failure", "not-found replacement confirmation", "locked/completed protection"]),
    2: ("schedule dependency validity", ["planning revision and per-day dependencies", "fact validity", "display/lock/complete/confirm/export", "atomic rejection and unchanged days"]),
    3: ("cross-city contract", ["daily city and endpoints", "fixed transport and whole-day budget", "shared missing/stale/conflict semantics"]),
    4: ("durable assistant clarification", ["original request and slots", "ownership and expiry", "continue/change/cancel", "proposal confirmation preserved"]),
    5: ("research evidence constraints", ["citation IDs and support", "source/date/scope/demo metadata", "distinct failure states", "independent evaluation limits"]),
    6: ("retrieval versus generation", ["retrieval zero answer-model sends", "visible answer/sources/fallback", "call accounting"]),
    7: ("upload resource governance", ["frequency/bytes/pending/disk limits", "atomic reservation", "same-user duplicate hash", "uncertain commit reconciliation", "release and storage isolation"]),
    8: ("deployment dataset identity", ["isolated new environment", "preserved existing mounts", "startup identity guard", "existing-data upgrade and rollback"]),
    9: ("scheduler ownership recovery", ["loss stops claims", "readiness and alert", "controlled recovery", "old-execution fencing"]),
    10: ("session and share security", ["redacted capability URLs", "cookie and CSRF", "expiry/revocation", "recovery and MFA", "private-trip authorization"]),
    11: ("fact completion loop", ["opening and reservations", "hotel endpoints and station lead time", "meals", "parent/child relations", "source/expiry and local replanning"]),
    12: ("modules and operating boundaries", ["Java responsibilities", "execution responsibilities", "result-page components", "resource/log/data retention", "alerts and automated off-host backup", "RPO/RTO", "security CI"]),
}

def update(phase="RUNNING", next_step="Implement and validate scopes 1-12"):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ledger_path = EVIDENCE / "scope-ledger.json"
    if not ledger_path.exists():
        ledger = {"baseline_sha": head, "started_at": now, "scope": []}
        for identity, (title, children) in SCOPE.items():
            ledger["scope"].append({"id": identity, "title": title, "status": "in_progress" if identity == 0 else "pending", "subitems": [{"name": c, "status": "pending", "implementation": [], "evidence": [], "limits": []} for c in children]})
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    completed = [r["id"] for r in ledger["scope"] if r["status"] == "complete"]
    blocked = [{"scope": r["id"], "item": child["name"], "limits": child.get("limits", [])}
               for r in ledger["scope"] for child in r["subitems"] if child["status"] == "blocked_external"]
    prior_path = COORD / "trip-status.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.exists() else {}
    status = {"project": "Trip", "phase": phase, "updated_at": now, "head_sha": head,
              "completed_ids": completed, "pending_ids": [i for i in SCOPE if i not in completed],
              "blocked_items": blocked, "next_step": next_step, "evidence_path": str(EVIDENCE),
              "deployment_window_needed": phase == "DEPLOY_READY", "final_sha": prior.get("final_sha"), "ci_state": prior.get("ci_state", "not_started")}
    COORD.mkdir(parents=True, exist_ok=True)
    temporary = COORD / "trip-status.json.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(COORD / "trip-status.json")
    print(json.dumps(status, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="RUNNING")
    parser.add_argument("--next-step", default="Implement and validate scopes 1-12")
    args = parser.parse_args()
    update(args.phase, args.next_step)
