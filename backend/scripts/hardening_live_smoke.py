"""One real one-day task in a new private database; logs remain outside the repo.

Uses the deployment's existing credentials and retry/timeout settings. Does not
change the opt-in evaluation policy or invent provider prices. Hard wall limit
also covers initialization. This is a smoke test, not a quality benchmark.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BACKEND = Path(__file__).resolve().parents[1]


def child(output):
    sys.path.insert(0, str(BACKEND))
    from datetime import date, timedelta
    from app.db.database import init_db, SessionLocal
    from app.db.models import TripTask, User
    from app.models.schemas import TripRequest
    from app.services import trip_tasks
    init_db()
    with SessionLocal() as db:
        db.add(User(id=1, username="isolated-smoke", hashed_password="no-login"))
        db.commit()
    day = (date.today() + timedelta(days=1)).isoformat()
    body = TripRequest(city="北京", start_date=day, end_date=day, travel_days=1,
                       transportation="公共交通", accommodation="经济型酒店", preferences=["历史文化"])
    identity, _ = trip_tasks.submit(1, body, "single-live-smoke")
    with SessionLocal() as db:
        task = db.get(TripTask, identity)
        task.status = "running"
        task.deadline_at = trip_tasks.now() + timedelta(seconds=90)
        db.commit()
    assert trip_tasks.llm_request_gate.try_acquire()
    trip_tasks.TripTaskRunner().execute(identity)
    state = trip_tasks.snapshot(1, identity)
    result = state.get("result", {})
    report = {"mode": "single_real_task_isolated_sqlite", "status": state["status"],
              "error_code": state["error_code"], "usage": state["usage"],
              "quality": result.get("quality"), "pois": [
                  {"id": a["poi_id"], "name": a["name"]}
                  for day in result.get("data", {}).get("days", []) for a in day["attractions"]],
              "limitations": ["One sample; no accuracy or availability claim", "No daily user records read or written",
                              "Provider cost not inferred when prices are absent"]}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(output):
    runtime = Path(tempfile.mkdtemp(prefix="trip-hardening-live-"))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"DATA_DIR": str(runtime), "CHROMA_DIR": str(runtime / "chroma"),
        "UPLOAD_DIR": str(runtime / "uploads"), "LOG_DIR": str(runtime / "logs"),
        "DATABASE_URL": "sqlite:///" + (runtime / "live.db").as_posix(), "APP_ENV": "development",
        "BOOTSTRAP_ADMIN_USERNAME": "", "PYTHONIOENCODING": "utf-8"}
    with (runtime / "private.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run([sys.executable, __file__, "--child", "--output", str(output)],
                cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=150)
            if not output.exists():
                output.write_text(json.dumps({"status": "initialization_failed", "exit_code": result.returncode}), encoding="utf-8")
        except subprocess.TimeoutExpired:
            output.write_text(json.dumps({"status": "wall_timeout", "limit_seconds": 150}), encoding="utf-8")
    print(json.dumps({"report": str(output), "status": json.loads(output.read_text(encoding="utf-8"))["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    (child if args.child else main)(args.output)
