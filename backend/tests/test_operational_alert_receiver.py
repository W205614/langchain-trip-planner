"""Exercise the production receiver's real HTTP and process shutdown boundaries."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


@pytest.mark.skipif(os.name != "posix", reason="Windows process termination does not deliver catchable POSIX SIGTERM")
def test_http_inbox_survives_clean_sigterm_exit(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 9088))
    inbox = tmp_path / "alerts.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts" / "operational_alert_receiver.py"
    environment = {**os.environ, "ALERT_INBOX": str(inbox), "PYTHONDONTWRITEBYTECODE": "1"}
    client = build_opener(ProxyHandler({}))
    process = subprocess.Popen([sys.executable, str(script)], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while True:
            assert process.poll() is None, "Receiver exited before serving HTTP"
            try:
                with client.open("http://127.0.0.1:9088/healthz", timeout=1) as response:
                    assert response.read() == b"ok"
                break
            except URLError:
                if time.monotonic() >= deadline:
                    pytest.fail("Receiver did not become ready")
                time.sleep(0.05)
        body = {"alerts": [{"status": "firing", "startsAt": "2026-11-08T00:00:00Z",
                            "labels": {"alertname": "IsolatedShutdownCheck", "job": "fixture"}}]}
        request = Request("http://127.0.0.1:9088/alerts", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        with client.open(request, timeout=2) as response:
            assert json.load(response) == {"accepted": 1}
        saved = inbox.read_bytes()
        assert json.loads(saved)["name"] == "IsolatedShutdownCheck"
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert inbox.read_bytes() == saved
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
