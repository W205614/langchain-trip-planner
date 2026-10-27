"""Window-only old daily snapshot and legal owned smoke, no provider calls."""
from pathlib import Path
from datetime import datetime, timezone
import contextlib
import io
import json
import secrets
import shutil
import subprocess
import sys
import time
import requests

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
COORD = Path(r'C:\Users\25452\.codex\visualizations\2026\10\25\01a172e2-8a39-7783-b536-b6dbe2f478cd\execution-20261026')
PRIVATE = ROOT / '.codex-optimization/daily-window'
PROJECT = 'langchain-trip-planner'
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from deployment_assets import services, docker, database_columns, database_summary, sql, validate_manifest
from backup_java_deployment import backup
from automated_maintenance import maintenance_lock


def grant():
    value = json.loads((COORD / 'deployment-window.json').read_text(encoding='utf-8'))
    assert value['owner'] == 'Trip' and value['state'] == 'granted'
    return value


def main():
    began = time.monotonic()
    window = grant()
    original = services(PROJECT)
    assert {v['Id'] for v in original.values()} == set(window['daily_rebuild_original_container_ids'])
    assert all(v['State']['Running'] for v in original.values())
    # Fail before any stop if an old image cannot be exported for rollback.
    for item in original.values():
        assert json.loads(docker('image', 'inspect', item['Image']))[0]['Id'] == item['Image']
    PRIVATE.mkdir(parents=True, exist_ok=False)
    (PRIVATE / 'original-inspect.json').write_text(json.dumps(original), encoding='utf-8')
    (PRIVATE / 'source-config').mkdir()
    for source in (ROOT / 'deploy/runtime').glob('*.env'):
        shutil.copy2(source, PRIVATE / 'source-config' / source.name)
    for n, source in enumerate(original['frontend']['Config']['Labels']['com.docker.compose.project.config_files'].split(',')):
        source = Path(source)
        if source.is_file():
            shutil.copy2(source, PRIVATE / 'source-config' / (str(n) + '-' + source.name))
    pg = original['postgres']
    env = dict(s.split('=', 1) for s in pg['Config']['Env'] if '=' in s)
    database, user = env['POSTGRES_DB'], env['POSTGRES_USER']
    columns = database_columns(pg['Id'], database, user)
    before = database_summary(pg['Id'], database, user, columns)
    (PRIVATE / 'before-smoke-columns.json').write_text(json.dumps(columns), encoding='utf-8')
    (PRIVATE / 'before-smoke-summary.json').write_text(json.dumps(before), encoding='utf-8')
    report = {'project': PROJECT, 'passed': False, 'stage': 'old_legal_smoke',
              'original_running': {k: v['State']['Running'] for k, v in original.items()},
              'original_container_ids': {k: v['Id'] for k, v in original.items()},
              'original_images': {k: v['Image'] for k, v in original.items()},
              'new_direct_paid_provider_calls': 0, 'smoke_registered': False,
              'backup': None, 'errors': []}
    output = EVIDENCE / 'daily-fresh-backup.json'
    assert not output.exists()
    def save():
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    stopped = []
    try:
        # Only one new, uniquely named account; preserve credentials before the
        # request so uncertain registration cannot cause account/sample swapping.
        credentials = {'username': 'daily_opt_' + secrets.token_hex(6),
                       'password': 'Daily!' + secrets.token_urlsafe(24),
                       'old_bearer': None, 'user_id': None, 'copied_trip_id': None}
        credential_path = PRIVATE / 'smoke-credentials.json'
        credential_path.write_text(json.dumps(credentials), encoding='utf-8')
        session = requests.Session(); session.trust_env = False
        base = 'http://127.0.0.1:8380'
        response = session.post(base + '/api/auth/register', json={k: credentials[k] for k in ('username', 'password')}, timeout=20)
        assert response.status_code == 200, 'Old daily registration failed; no credential response printed'
        report['smoke_registered'] = True; save()
        response = session.post(base + '/api/auth/login', json={k: credentials[k] for k in ('username', 'password')}, timeout=20)
        assert response.status_code == 200
        credentials['old_bearer'] = response.json()['access_token']
        headers = {'Authorization': 'Bearer ' + credentials['old_bearer']}
        me = session.get(base + '/api/auth/me', headers=headers, timeout=20)
        assert me.status_code == 200
        credentials['user_id'] = me.json()['id']
        cards = session.get(base + '/api/community/cards?page=1&pageSize=1', timeout=20)
        assert cards.status_code == 200
        published = cards.json()['data']
        if published:
            copied = session.post(base + '/api/community/cards/' + str(published[0]['id']) + '/copy', headers=headers, json={}, timeout=20)
            assert copied.status_code == 200
            credentials['copied_trip_id'] = copied.json()['id']
        credential_path.write_text(json.dumps(credentials), encoding='utf-8')
        report['owned_public_card_copy_created'] = credentials['copied_trip_id'] is not None
        report['legal_old_password_and_bearer_saved_privately'] = True
        report['stage'] = 'stop_old_writers'; save()
        with maintenance_lock(PROJECT):
            for name in ('frontend', 'backend', 'agent'):
                grant(); docker('stop', '--time', '30', original[name]['Id']); stopped.append(name)
            assert not any(services(PROJECT)[s]['State']['Running'] for s in ('backend', 'agent'))
            report['stage'] = 'fresh_complete_backup'; save()
            destination = ROOT / 'backups' / ('pre-optimization-daily-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = backup(destination, PROJECT, archive_images=True)
            validate_manifest(destination)
            shutil.copytree(PRIVATE / 'source-config', destination / 'original-source-config')
            # Additional source configs are private recovery material; source
            # manifest remains unchanged and its original protected files verify.
            report.update(backup=str(destination), manifest_verified=True,
                          images_exported=True, writers_stopped_for_enrollment=True,
                          stage='complete', passed=True,
                          manifest_file_count=len(manifest['sha256']), original_table_count=len(columns), elapsed_seconds=round(time.monotonic() - began, 3))
            (PRIVATE / 'latest-backup.txt').write_text(str(destination), encoding='utf-8')
    except Exception as error:
        report['errors'].append({'stage': report['stage'], 'type': type(error).__name__})
        # Stage1 never changes schema or images. On failure restore only exact
        # original services, leaving the failed backup and private state intact.
        for name in reversed(stopped):
            docker('start', original[name]['Id'])
        raise
    finally:
        save()
    print(json.dumps({'passed': report['passed'], 'backup': report['backup'],
                      'writers_stopped': report.get('writers_stopped_for_enrollment'),
                      'new_smoke_account': True, 'owned_copy': report.get('owned_public_card_copy_created'),
                      'new_provider_calls': 0}))


if __name__ == '__main__':
    raise SystemExit(main())
