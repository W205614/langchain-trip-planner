"""Prepare one synthetic administrator only inside the isolated provider fixture."""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import struct
import time
from uuid import uuid4
import httpx
from deployment_assets import services, sql


def code(secret):
    raw = base64.b32decode(secret + '=' * ((8-len(secret) % 8) % 8))
    digest = hmac.new(raw, struct.pack('>Q', int(time.time())//30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff) % 1000000).zfill(6)


def prepare(base_url, output, project='trip-opt-candidate-20261026', allow_optional_admin_mfa=False):
    root = Path(__file__).resolve().parents[2]
    if not output.resolve().is_relative_to(root / '.codex-optimization'):
        raise ValueError('Synthetic authentication material must stay in the ignored local directory')
    if project not in {'trip-opt-candidate-20261026', 'trip-opt-candidate-20261026-v2', 'trip-validation'}:
        raise ValueError('Only the explicitly isolated candidate projects are eligible')
    selected = services(project)
    backend=selected['backend']
    environment=dict(value.split('=',1) for value in backend['Config']['Env'] if '=' in value)
    if environment.get('APP_ENV')!='validation' or environment.get('VALIDATION_ALLOW_FIXTURES')!='yes':
        raise ValueError('Explicit validation environment and provider fixtures required')
    if project!='trip-validation' and backend['Config']['Labels'].get('com.tripplanner.validation') != 'candidate':
        raise ValueError('Candidate container label required')
    with httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client:
        marker = client.get('/api/validation/fixture'); marker.raise_for_status()
        if marker.json().get('offline_fixture') is not True:
            raise ValueError('Offline fixture marker required')
        username, password = 'admin_' + uuid4().hex[:18], 'Isolated!Admin_2026_' + uuid4().hex[:12]
        result = client.post('/api/auth/register', json={'username': username, 'password': password}); result.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + result.json()['access_token']
        uid = client.get('/api/auth/me'); uid.raise_for_status(); identity = uid.json()['id']
        sql(selected['postgres']['Id'], 'trip', f'UPDATE users SET is_admin=true WHERE id={int(identity)}')
        security=client.get('/api/auth/security');security.raise_for_status()
        required=security.json()['admin_mfa_required']
        if not required and not allow_optional_admin_mfa:
            raise AssertionError('This candidate must require administrator MFA')
        # Management is blocked until this actual account completes its MFA flow.
        blocked = client.get('/api/knowledge/admin/submissions')
        if blocked.status_code != (403 if required else 200):
            raise AssertionError('Unenrolled administrator management must match the explicitly declared MFA policy')
        result = client.post('/api/auth/mfa/enroll', json={'password': password}); result.raise_for_status()
        secret = result.json()['secret']
        confirmed_step = int(time.time())//30
        result = client.post('/api/auth/mfa/confirm', json={'totp_code': code(secret)}); result.raise_for_status()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({'OPTIMIZATION_ADMIN_USERNAME': username, 'OPTIMIZATION_ADMIN_PASSWORD': password,
                                      'OPTIMIZATION_ADMIN_TOTP_SECRET': secret}, indent=2), encoding='utf-8')
        return {'synthetic_admin_ready': True, 'admin_mfa_required':required,'unenrolled_management_status': blocked.status_code,
                'next_login_after_epoch': (confirmed_step + 1)*30,
                'credentials': 'ignored local file only; never included in evidence'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', default='http://127.0.0.1:18086')
    p.add_argument('--output', type=Path, default=Path('.codex-optimization/admin.json'))
    p.add_argument('--project', default='trip-opt-candidate-20261026')
    p.add_argument('--allow-optional-admin-mfa',action='store_true',help='Explicit legacy CI-fixture preparation only; default candidates require MFA')
    a = p.parse_args(); print(json.dumps(prepare(a.base_url, a.output, a.project,a.allow_optional_admin_mfa)))
