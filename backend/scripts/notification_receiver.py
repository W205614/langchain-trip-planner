"""Validation-only bounded notification recorder; discards arbitrary alert details."""
import json
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

events = deque(maxlen=100)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(list(events)).encode())

    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        if size < 1 or size > 65536:
            self.send_error(413)
            return
        body = json.loads(self.rfile.read(size))
        events.append({"status": body.get("status"), "alerts": [
            {"name": a.get("labels", {}).get("alertname"), "status": a.get("status")}
            for a in body.get("alerts", [])]})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9088), Handler).serve_forever()
