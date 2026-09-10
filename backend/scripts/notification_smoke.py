"""Exercise real Prometheus -> Alertmanager -> local receiver with a stopped test backend."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-p", "trip-validation", "-f", str(ROOT / "docker-compose.validation.yml")]


def wait_status(status, timeout=110):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urllib.request.urlopen("http://127.0.0.1:19088", timeout=5) as response:
            events = json.load(response)
        if any(a["name"] == "TripServiceDown" and a["status"] == status
               for event in events for a in event["alerts"]):
            return True
        time.sleep(2)
    raise RuntimeError("Notification missing: " + status)


if __name__ == "__main__":
    started = time.monotonic()
    # A fresh receiver prevents a previous run's events from satisfying this run.
    subprocess.run(COMPOSE + ["up", "-d", "--force-recreate", "notification-receiver"], check=True)
    subprocess.run(COMPOSE + ["up", "-d", "notification-receiver", "alertmanager", "prometheus"], check=True)
    # Establish a real scrape before stopping the dependency.
    time.sleep(10)
    try:
        subprocess.run(COMPOSE + ["stop", "backend"], check=True)
        wait_status("firing")
    finally:
        subprocess.run(COMPOSE + ["start", "backend"], check=True)
    wait_status("resolved")
    report = {"firing_delivered": True, "resolved_delivered": True,
              "seconds": round(time.monotonic() - started, 2), "boundary": "Local isolated Docker notification channel"}
    (ROOT / "docs/evidence/notifications.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
