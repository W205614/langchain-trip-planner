"""Isolated HTTP alert/probe recorder; never a production notification endpoint."""
import json
import os
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import count

if os.environ.get("APP_ENV") != "validation" or os.environ.get("VALIDATION_ALLOW_FIXTURES") != "yes":
    raise RuntimeError("This receiver is restricted to the validation stack")

events = deque(maxlen=100)
event_ids = count(1)
state = {"probe_up": True, "fail_delivery": False}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, media="application/json"):
        content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/metrics":
            self.respond(200 if state["probe_up"] else 503, "edge_validation_probe 1\n", "text/plain")
        elif self.path == "/events":
            self.respond(200, {"state": state, "events": list(events)})
        else:
            self.respond(404, {})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= 65536:
            self.respond(413, {})
            return
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, TypeError):
            self.respond(400, {})
            return
        if self.path == "/control":
            if not isinstance(body, dict) or any(key not in state or type(value) is not bool for key, value in body.items()):
                self.respond(422, {})
                return
            state.update(body)
            self.respond(200, state)
        elif self.path == "/alerts":
            accepted = not state["fail_delivery"]
            events.append({"id": next(event_ids), "status": body.get("status"), "accepted": accepted,
                           "authorization_matched": self.headers.get("Authorization") == "Bearer validation-webhook-only",
                           "alerts": [{"name": alert.get("labels", {}).get("alertname"), "status": alert.get("status")}
                                      for alert in body.get("alerts", [])[:20]]})
            self.respond(200 if accepted else 503, {"accepted": accepted})
        else:
            self.respond(404, {})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9088), Handler).serve_forever()
