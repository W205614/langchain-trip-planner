"""Exercise one rebuilt Alertmanager through its real v2 API and webhook v4.

Only UUID-named temporary containers and an internal network are created. No
existing containers, volumes, configuration or notification channels are used.
Fixture files remain under .cache for review. Local Docker objects are retained
by default, with fixture containers stopped after verification. Explicit cleanup
is reserved for a CI runner's own ephemeral environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=90)
    if check and result.returncode:
        raise RuntimeError('Docker fixture operation failed: ' + result.stderr[-1000:])
    return result


def timestamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def exercise(image, receiver_image, cleanup_owned_resources=False):
    identity = 'trip-monitor-smoke-' + uuid.uuid4().hex[:12]
    root = (Path(__file__).resolve().parents[1] / '.cache' / identity)
    root.mkdir(parents=True)
    receiver, alertmanager = identity + '-receiver', identity + '-alertmanager'
    config = root / 'alertmanager.yml'
    config.write_text('''global:
  resolve_timeout: 1m
route:
  receiver: isolated-fixture
  group_by: [alertname]
  group_wait: 100ms
  group_interval: 100ms
  repeat_interval: 1m
receivers:
  - name: isolated-fixture
    webhook_configs:
      - url: http://receiver:9080/alerts
        send_resolved: true
''', encoding='utf-8')
    script = root / 'receiver.py'
    script.write_text('''from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        if self.path!='/alerts' or not 0<size<65536:
            self.send_error(400);return
        event=json.loads(self.rfile.read(size))
        with open('/tmp/events.jsonl','a',encoding='utf-8') as target:
            target.write(json.dumps(event)+'\\n')
        self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    def log_message(self,*args):pass
HTTPServer(('0.0.0.0',9080),Handler).serve_forever()
''', encoding='utf-8')
    containers = []
    network_created = False
    evidence = {'fixture': identity, 'image': image, 'status': 'failed',
                'scope': 'isolated real Alertmanager HTTP v2 -> webhook v4 firing and resolved; no external recipient'}
    try:
        # Require intended images to exist; this tool never silently selects a tag.
        evidence['image_id'] = json.loads(docker('image', 'inspect', '--format', '{{json .Id}}', image).stdout)
        docker('image', 'inspect', receiver_image)
        network_id = docker('network', 'create', '--internal', identity).stdout.strip()
        evidence['network'] = {'name': identity, 'id': network_id}
        network_created = True
        receiver_result = docker('run', '--detach', '--name', receiver, '--network', identity,
               '--network-alias', 'receiver', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--mount', f'type=bind,source={script},target=/fixture.py,readonly',
               '--entrypoint', 'python', receiver_image, '-u', '/fixture.py')
        containers.append(receiver)
        evidence['containers'] = [{'name': receiver, 'id': receiver_result.stdout.strip(), 'business_data': False}]
        alertmanager_result = docker('run', '--detach', '--name', alertmanager, '--network', identity,
               '--network-alias', 'alertmanager', '--read-only',
               '--tmpfs', '/alertmanager:rw,noexec,nosuid,size=32m,uid=65534,gid=65534',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--mount', f'type=bind,source={config},target=/etc/alertmanager/alertmanager.yml,readonly',
               image, '--config.file=/etc/alertmanager/alertmanager.yml',
               '--storage.path=/alertmanager', '--cluster.listen-address=')
        containers.append(alertmanager)
        evidence['containers'].append({'name': alertmanager, 'id': alertmanager_result.stdout.strip(), 'business_data': False})
        # Call through the internal receiver network; no host port is opened.
        def http(path, body=None):
            encoded = json.dumps(body).encode() if body is not None else None
            code = ("from urllib.request import Request,urlopen; import json; "
                    f"q=Request({'http://alertmanager:9093' + path!r},data={encoded!r},"
                    "headers={'Content-Type':'application/json'}); "
                    "r=urlopen(q,timeout=3); "
                    "print(json.dumps({'status':r.status,'body':r.read().decode()}))")
            output = docker('exec', receiver, 'python', '-c', code, check=False)
            return json.loads(output.stdout) if output.returncode == 0 else None
        ready = False
        for _ in range(60):
            response = http('/-/ready')
            ready = response is not None and response['status'] == 200
            if ready:
                break
            time.sleep(.5)
        if not ready:
            raise RuntimeError('Alertmanager never became ready')
        response = http('/api/v2/status')
        evidence['reported_version'] = json.loads(response['body'])['versionInfo']
        if evidence['reported_version'].get('version') != '0.34.1':
            raise RuntimeError('Unexpected Alertmanager application version')
        now = datetime.now(timezone.utc)
        alert = {'labels': {'alertname': 'TripSecuritySmoke', 'severity': 'warning', 'job': identity},
                 'annotations': {'summary': 'Isolated security build compatibility fixture'},
                 'startsAt': timestamp(now - timedelta(seconds=1)),
                 'endsAt': timestamp(now + timedelta(minutes=5))}

        def submit():
            response = http('/api/v2/alerts', [alert])
            if response is None or response['status'] not in (200, 202):
                raise RuntimeError('Alert submission failed')

        def await_event(status):
            for _ in range(60):
                output = docker('exec', receiver, 'python', '-c',
                                "from pathlib import Path; p=Path('/tmp/events.jsonl'); print(p.read_text() if p.exists() else '')", check=False)
                events = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
                for event in events:
                    if event.get('version') == '4' and event.get('status') == status and any(
                            item.get('labels', {}).get('job') == identity for item in event.get('alerts', [])):
                        return event
                time.sleep(.5)
            raise RuntimeError('Expected webhook event missing: ' + status)

        submit()
        evidence['firing'] = await_event('firing')
        alert['endsAt'] = timestamp(datetime.now(timezone.utc) - timedelta(milliseconds=1))
        submit()
        evidence['resolved'] = await_event('resolved')
        evidence['status'] = 'pass'
    finally:
        # Exact UUID-owned objects only; no prune, global enumeration or volumes.
        evidence['cleanup'] = []
        for container in reversed(containers):
            logs = docker('logs', container, check=False)
            (root / (container + '.log')).write_text(logs.stdout + logs.stderr, encoding='utf-8')
            action = ('rm', '--force', '--volumes') if cleanup_owned_resources else ('stop',)
            evidence['cleanup'].append({'container': container, 'action': 'remove_explicit_ci' if cleanup_owned_resources else 'stop_and_retain',
                                        'exit': docker(*action, container, check=False).returncode})
        if network_created:
            evidence['cleanup'].append({'network': identity, 'action': 'remove_explicit_ci' if cleanup_owned_resources else 'retain',
                                        'exit': docker('network', 'rm', identity, check=False).returncode if cleanup_owned_resources else 0})
        (root / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if any(item['exit'] != 0 for item in evidence['cleanup']):
        raise RuntimeError('Fixture completed but exact-object cleanup failed; inspect retained evidence')
    print(json.dumps({'status': evidence['status'], 'image_id': evidence['image_id'], 'evidence': str(root / 'result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--receiver-image', default='python:3.11-slim-trixie')
    parser.add_argument('--cleanup-owned-resources', action='store_true', help='CI ephemeral runner only; local default retains stopped UUID-owned containers and internal network')
    args = parser.parse_args()
    exercise(args.image, args.receiver_image, args.cleanup_owned_resources)
