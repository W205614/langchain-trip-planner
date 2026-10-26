"""Bounded durable local inbox. External human delivery requires an explicitly configured receiver."""
import hashlib
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

INBOX = Path(os.environ.get("ALERT_INBOX", "/app/runtime/alerts.jsonl"))
MAX_BYTES = 2 * 1024 * 1024

def record(body, inbox=INBOX):
    alerts = []
    for alert in body.get("alerts", [])[:100]:
        labels = alert.get("labels", {})
        item = {"name": str(labels.get("alertname", ""))[:100], "job": str(labels.get("job", ""))[:64],
                "status": str(alert.get("status", ""))[:16], "starts_at": str(alert.get("startsAt", ""))[:50]}
        item["event_id"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        alerts.append(item)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    lines = inbox.read_text(encoding="utf-8").splitlines() if inbox.exists() else []
    known = {json.loads(line).get("event_id") for line in lines}
    new = [{**item, "received_at": datetime.now(timezone.utc).isoformat()} for item in alerts if item["event_id"] not in known]
    lines.extend(json.dumps(item, ensure_ascii=False) for item in new)
    while len(lines) > 1000 or sum(len(line.encode()) + 1 for line in lines) > MAX_BYTES:
        lines.pop(0)
    temporary = inbox.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    temporary.replace(inbox)
    return len(new)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/healthz": self.send_error(404); return
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        if self.path != "/alerts" or not 0 < size <= 65536: self.send_error(413); return
        try: count = record(json.loads(self.rfile.read(size)))
        except (ValueError, TypeError, KeyError): self.send_error(400); return
        self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({"accepted": count}).encode())
    def log_message(self, *args): pass

if __name__ == "__main__": HTTPServer(("0.0.0.0", 9088), Handler).serve_forever()
