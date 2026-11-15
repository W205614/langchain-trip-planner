"""Verify the real frontend proxy follows Docker DNS changes without a reload.

Only uniquely named fixture containers and an isolated network are used. The
HTTP fixture echoes requests; it never accesses application data or providers.
Created containers are stopped and retained with the report for inspection.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result.stdout.strip()


SERVER = '''from http.server import BaseHTTPRequestHandler, HTTPServer
import json,os
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.respond()
    def do_POST(self): self.respond()
    def respond(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0))).decode()
        value={'identity':os.environ['IDENTITY'],'method':self.command,
               'path':self.path,'body':body,'request_id':self.headers.get('X-Request-ID')}
        content=(b'data: proxy-stream-ok\\n\\n' if self.path.startswith('/api/stream')
                 else json.dumps(value).encode())
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream' if self.path.startswith('/api/stream') else 'application/json')
        self.send_header('Content-Length',str(len(content)))
        self.end_headers();self.wfile.write(content);self.wfile.flush()
HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
'''


def exercise(frontend_image, python_image, output, packaged_config=False):
    root = Path(__file__).resolve().parents[1]
    identity = "trip-nginx-dns-" + uuid.uuid4().hex[:10]
    existing = docker("network", "ls", "-q").split()
    networks = json.loads(docker("network", "inspect", *existing)) if existing else []
    used = [ipaddress.ip_network(c["Subnet"]) for n in networks
            for c in (n.get("IPAM", {}).get("Config") or []) if c.get("Subnet")]
    candidates = [ipaddress.ip_network(f"10.252.{i}.0/24") for i in range(200, 250)]
    subnet = next(n for n in candidates if not any(n.version == x.version and n.overlaps(x) for x in used))
    network = identity + "-net"
    before, frontend, after = identity + "-before", identity + "-frontend", identity + "-after"
    created = []
    report = {"passed": False, "network": network, "subnet": str(subnet), "checks": [],
              "config_source": "packaged_image" if packaged_config else "source_bind"}
    try:
        docker("network", "create", "--subnet", str(subnet), network)
        def server(name, address, value):
            docker("run", "-d", "--name", name, "--network", network, "--network-alias", "backend",
                   "--ip", address, "-e", "IDENTITY=" + value, "--entrypoint", "python", python_image,
                   "-u", "-c", SERVER)
            created.append(name)
        server(before, str(subnet.network_address + 2), "before")
        config_mount = [] if packaged_config else [
            "--mount", f"type=bind,source={root / 'frontend/nginx.conf'},target=/etc/nginx/conf.d/default.conf,readonly"]
        docker("run", "-d", "--name", frontend, "--network", network, "--ip", str(subnet.network_address + 10),
               *config_mount,
               "-p", "127.0.0.1::80", "--entrypoint", "nginx", frontend_image, "-g", "daemon off;")
        created.append(frontend)
        container = json.loads(docker("inspect", frontend))[0]
        port = container["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port
        frontend_id = container["Id"]
        started = container["State"]["StartedAt"]
        def request(path, data=None):
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data), timeout=4) as response:
                return response.read(), dict(response.headers)
        def wait_identity(value):
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                try:
                    body, _ = request("/readyz")
                    if json.loads(body)["identity"] == value:
                        return
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(.3)
            raise AssertionError("Proxy did not resolve backend " + value)
        wait_identity("before")
        report["checks"].append("initial health proxy")
        docker("network", "disconnect", network, before)
        server(after, str(subnet.network_address + 3), "after")
        changed = time.monotonic()
        wait_identity("after")
        report["dns_recovery_seconds"] = round(time.monotonic() - changed, 2)
        report["checks"].append("backend IP .2 to .3 without frontend reload")
        for path in ("/api/echo?value=a%2Fb&second=x+y", "/api/shared-trips/token-fixture?value=a%2Fb"):
            body, headers = request(path, b"request-body")
            value = json.loads(body)
            assert value["identity"] == "after" and value["path"] == path
            assert value["method"] == "POST" and value["body"] == "request-body"
            assert value["request_id"]
            if "shared-trips" in path:
                assert headers["Cache-Control"] == "no-store"
            report["checks"].append("URI/body/request-id preserved: " + path.split("?")[0])
        stream, headers = request("/api/stream")
        assert stream == b"data: proxy-stream-ok\n\n" and headers["Content-Type"] == "text/event-stream"
        report["checks"].append("SSE response preserved")
        for path in ("/metrics", "/internal/secret", "/actuator/prometheus"):
            try:
                request(path)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("Private path exposed: " + path)
        report["checks"].append("private routes remain 404")
        final = json.loads(docker("inspect", frontend))[0]
        assert final["Id"] == frontend_id and final["State"]["StartedAt"] == started
        report.update(passed=True, frontend_not_restarted=True, containers=created)
    finally:
        for name in reversed(created):
            docker("stop", "--time", "5", name)
        report["containers_stopped"] = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packaged-config", action="store_true",
                        help="Exercise the candidate image's packaged Nginx configuration without a source bind")
    args = parser.parse_args()
    print(json.dumps(exercise(args.frontend_image, args.python_image, args.output, args.packaged_config)))
