"""Acceptance for the explicitly isolated trip-public-edge Compose project only.

Requires the project's Python dependencies and cryptography for temporary SMTP TLS.
Never installs a host CA, loads daily credentials, or deletes containers/volumes.
"""
import argparse
from datetime import datetime, timedelta, timezone
import http.cookiejar
import http.client
import json
from pathlib import Path
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache/public-edge-validation"
COMPOSE = ["docker", "compose", "-p", "trip-public-edge", "-f", str(ROOT / "docker-compose.public-edge-validation.yml")]


def compose(*args):
    return subprocess.check_output(COMPOSE + list(args), cwd=ROOT, text=True, encoding="utf-8", timeout=45)


def remote(service, script):
    return compose("exec", "-T", service, "python", "-c", script)


def remote_http(service, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    return remote(service, "import urllib.request; "
                  f"print(urllib.request.urlopen(urllib.request.Request({url!r},data={data!r},"
                  "headers={'Content-Type':'application/json'}),timeout=5).read().decode())")


def wait(label, predicate, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            print(label + ": PASS", flush=True)
            return result
        time.sleep(2)
    raise AssertionError(label + " timed out")


def save(name, result):
    (CACHE / (name + "-result.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


def prepare():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from prepare_external_alerts import prepare as render_alerts

    tls = CACHE / "tls"
    tls.mkdir(parents=True, exist_ok=True)
    if not (tls / "smtp-ca.crt").exists():
        now = datetime.now(timezone.utc)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Trip isolated SMTP CA")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
              .not_valid_after(now + timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
              .sign(key, hashes.SHA256()))
        leafkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mailpit")]))
                .issuer_name(name).public_key(leafkey.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("mailpit"), x509.DNSName("localhost")]), False)
                .sign(key, hashes.SHA256()))
        (tls / "smtp-ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (tls / "combined-ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (tls / "mailpit.crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        private = tls / "mailpit.key"
        private.write_bytes(leafkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
        private.chmod(0o600)
    source = CACHE / "alert-source.env"
    if not source.exists():
        source.write_text("SMTP_HOST=mailpit\nSMTP_PORT=1025\nSMTP_SECURITY=starttls\n"
                          "SMTP_FROM=no-reply@example.test\nALERT_EMAIL_TO=edge-validation@example.test\n"
                          "ALERT_WEBHOOK_URL=https://edge/alerts\nALERT_WEBHOOK_TOKEN=validation-webhook-only\n", encoding="utf-8")
        source.chmod(0o600)
    output = CACHE / "alertmanager.yml"
    if not output.exists():
        render_alerts(source, output)
        config = yaml.safe_load(output.read_text(encoding="utf-8"))
        # Only the acceptance fixture shortens the production grouping intervals.
        config["route"].update(group_wait="1s", group_interval="2s", repeat_interval="1h")
        output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    print("Private fixture prepared; existing certificates/configuration retained.")


def trust():
    compose("cp", "edge:/data/caddy/pki/authorities/local/root.crt", str(CACHE / "tls/caddy-root.crt"))
    tls = CACHE / "tls"
    (tls / "combined-ca.pem").write_bytes((tls / "smtp-ca.crt").read_bytes() + (tls / "caddy-root.crt").read_bytes())
    print("Caddy CA copied for this test only; host trust store unchanged.")


def https():
    plain = http.client.HTTPConnection("localhost", 18082, timeout=5)
    plain.request("GET", "/api/auth/options")
    redirect = plain.getresponse()
    assert redirect.status == 308 and redirect.getheader("Location").startswith("https://localhost"), redirect.status
    redirect.read()
    plain.close()
    context = ssl.create_default_context(cafile=str(CACHE / "tls/caddy-root.crt"))
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),
                                        urllib.request.HTTPCookieProcessor(jar), urllib.request.ProxyHandler({}))

    def request(path, body=None, headers=None):
        req = urllib.request.Request("https://localhost:18443" + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Origin": "https://localhost:18443", **(headers or {})})
        try:
            response = opener.open(req, timeout=10)
        except urllib.error.HTTPError as ex:
            response = ex
        with response:
            return response.code, response.headers, response.read().decode()

    username = "edge" + uuid.uuid4().hex[:10]
    status, headers, _ = request("/api/auth/register", {"username": username, "password": "EdgeAcceptance!42",
                                "email": username + "@example.test"}, {"X-Auth-Mode": "cookie",
                                "X-Forwarded-For": "198.51.100.77", "X-Forwarded-Proto": "http",
                                "X-Forwarded-Host": "attacker.example"})
    assert status == 200, status
    cookies = headers.get_all("Set-Cookie")
    assert len(cookies) == 2 and all("Secure" in c and "SameSite=Strict" in c for c in cookies)
    assert "HttpOnly" in next(c for c in cookies if c.startswith("trip_session="))
    assert headers.get("Strict-Transport-Security") == "max-age=31536000"
    csrf = next(c.value for c in jar if c.name == "trip_csrf")
    assert request("/api/auth/me")[0] == 200
    assert request("/api/account/exports", {})[0] == 403
    assert request("/api/account/exports", {}, {"X-CSRF-Token": "invalid"})[0] == 403
    assert request("/api/account/exports", {}, {"X-CSRF-Token": csrf})[0] == 202
    assert all(request(path)[0] == 404 for path in ["/internal/v2/outbound/reserve", "/metrics", "/actuator/health"])
    jar.clear()
    statuses = [request("/api/auth/login", {"username": "missing" + uuid.uuid4().hex[:10], "password": "EdgeAcceptance!42"},
                        {"X-Forwarded-For": f"198.51.100.{i+1}", "X-Real-IP": f"203.0.113.{i+1}",
                         "X-Forwarded-Proto": "http"})[0] for i in range(12)]
    assert statuses == [401] * 10 + [429] * 2, statuses
    # A peer outside the edge subnet cannot promote plain HTTP to HTTPS or forge the client IP.
    direct = json.loads(remote("agent", "import urllib.request,json,uuid; "
        "body=json.dumps({'username':'direct'+uuid.uuid4().hex[:10],'password':'EdgeAcceptance!42','email':'direct'+uuid.uuid4().hex[:10]+'@example.test'}).encode(); "
        "r=urllib.request.urlopen(urllib.request.Request('http://frontend/api/auth/register',data=body,headers="
        "{'Host':'localhost','Content-Type':'application/json','X-Auth-Mode':'cookie','X-Forwarded-For':'198.51.100.88','X-Forwarded-Proto':'https'}),timeout=10); "
        "print(json.dumps({'status':r.status,'secure':any('Secure' in x for x in r.headers.get_all('Set-Cookie'))}))"))
    assert direct == {"status": 200, "secure": False}, direct
    independent = json.loads(remote("amap-fixture", "import urllib.request,urllib.error,json,uuid; "
        "q=urllib.request.Request('http://frontend/api/auth/login',data=json.dumps({'username':'missing'+uuid.uuid4().hex[:10],'password':'EdgeAcceptance!42'}).encode(),headers={'Host':'localhost','Content-Type':'application/json','X-Forwarded-For':'198.51.100.88'}); "
        "exec(\"try:\\n r=urllib.request.urlopen(q,timeout=10)\\nexcept urllib.error.HTTPError as e:\\n r=e\"); "
        "print(json.dumps({'status':r.code}))"))
    assert independent["status"] == 401, independent
    logs = compose("logs", "--no-log-prefix", "frontend")
    records = [json.loads(line) for line in logs.splitlines() if line.startswith('{"time"')]
    assert not any(row["remote"].startswith(("198.51.100.", "203.0.113.")) for row in records)
    save("https", {"http_redirect": 308, "secure_session_and_csrf_cookies": True, "hsts": True, "csrf_missing_invalid_valid": [403, 403, 202],
                   "protected_routes": 404, "forged_header_login_statuses": statuses,
                   "independent_peer_login_after_first_peer_rate_limited": independent["status"],
                   "untrusted_direct_forwarded_https_ignored": direct, "forged_remote_absent_from_nginx": True})


def alerts():
    checked = compose("exec", "-T", "alertmanager", "/bin/amtool", "check-config", "/etc/alertmanager/alertmanager.yml")
    assert "SUCCESS" in checked

    def control(**state):
        remote_http("webhook", "http://127.0.0.1:9088/control", state)

    def events():
        return json.loads(remote_http("webhook", "http://127.0.0.1:9088/events"))["events"]

    def mail():
        with urllib.request.urlopen("http://127.0.0.1:18028/api/v1/messages", timeout=5) as response:
            return json.load(response)["messages"]

    def failures():
        totals = {"email": 0, "webhook": 0}
        for line in remote_http("agent", "http://alertmanager:9093/metrics").splitlines():
            if line.startswith("alertmanager_notifications_failed_total{"):
                for kind in totals:
                    if f'integration="{kind}"' in line:
                        totals[kind] += float(line.rsplit(" ", 1)[1])
        return totals

    control(probe_up=True, fail_delivery=False)
    # Permit the real rule to settle before inducing a distinct incident.
    wait("probe initially up", lambda: any(item["value"][1] == "1" for item in json.loads(remote_http("agent", "http://prometheus:9090/api/v1/query?query=up%7Bjob%3D%22edge-validation-probe%22%7D"))["data"]["result"]))
    time.sleep(3)
    start = max((event["id"] for event in events()), default=0)
    def since(mark):
        return [event for event in events() if event["id"] > mark]
    before_mail = {m["ID"] for m in mail()}
    control(probe_up=False)
    wait("HTTPS Webhook firing", lambda: any(e["status"] == "firing" and e["accepted"] and e["authorization_matched"] for e in since(start)))
    wait("STARTTLS SMTP firing", lambda: any(m["ID"] not in before_mail and "[FIRING" in m["Subject"] for m in mail()))
    control(probe_up=True)
    wait("HTTPS Webhook resolved", lambda: any(e["status"] == "resolved" and e["accepted"] for e in since(start)))
    wait("STARTTLS SMTP resolved", lambda: any(m["ID"] not in before_mail and "[RESOLVED" in m["Subject"] for m in mail()))
    baseline = failures()
    failed_start = max((event["id"] for event in events()), default=0)
    compose("stop", "mailpit")
    try:
        control(probe_up=False, fail_delivery=True)
        wait("Webhook HTTP 503 observed", lambda: any(not e["accepted"] for e in since(failed_start)))
        failed = wait("SMTP and Webhook native failure counters", lambda: (current if all(current[k] > baseline[k] for k in current) else None)
                      if (current := failures()) else None)
    finally:
        compose("start", "mailpit")
    control(fail_delivery=False)
    wait("Webhook retry accepted", lambda: any(e["accepted"] and e["status"] == "firing" for e in since(failed_start)))
    wait("SMTP retry after restart", lambda: any("[FIRING" in m["Subject"] for m in mail()))
    control(probe_up=True)
    wait("Webhook recovery after failure", lambda: any(e["status"] == "resolved" and e["accepted"] for e in since(failed_start)))
    wait("SMTP recovery after failure", lambda: any("[RESOLVED" in m["Subject"] for m in mail()))
    inbox = remote("alert-receiver", "from pathlib import Path; print(Path('/app/runtime/alerts.jsonl').read_text())")
    retained = [json.loads(line) for line in inbox.splitlines() if line]
    assert {"firing", "resolved"}.issubset({item["status"] for item in retained})
    assert len({item["event_id"] for item in retained}) == len(retained)
    save("alerts", {"native_config_valid": True, "real_prometheus_probe": True, "webhook_events": since(start),
                    "failed_counter_before": baseline, "failed_counter_after": failed,
                    "smtp_subjects": [m["Subject"] for m in mail()], "deduplicated_local_inbox_events": len(retained),
                    "tls": "SMTP STARTTLS and HTTPS Webhook both verify their explicit local CAs"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "trust", "https", "alerts"])
    globals()[parser.parse_args().command]()
