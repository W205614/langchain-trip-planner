"""Root's fixed isolated candidate launcher; no remove/recreate/pull/load."""
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
PROJECT = 'trip-opt-hardened-20261026'
REVISION = '220257ad32e50d08cb987d565882f8a038e36c6f'


def docker(*arguments, timeout=30):
    result = subprocess.run(['docker', *arguments], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Docker operation failed; private command diagnostics intentionally omitted')
    return result.stdout


def main():
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == REVISION
    assert not docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT).strip(), 'Refuse existing candidate/recreation'
    manifest = {'business_sha': REVISION, 'built_at': datetime.now(timezone.utc).isoformat(), 'images': {}}
    for name in ['backend', 'agent', 'frontend', 'prometheus', 'alertmanager', 'postgres']:
        tag = 'postgres:17.11-alpine' if name == 'postgres' else 'trip-opt-' + name + ':220257a'
        item = json.loads(docker('image', 'inspect', tag))[0]
        revision = (item['Config'].get('Labels') or {}).get('org.opencontainers.image.revision')
        if name != 'postgres':
            assert revision == REVISION, 'Image revision mismatch'
        else:
            assert item['Id'] == 'sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b'
        manifest['images'][name] = {'tag': tag, 'id': item['Id'], 'revision': revision,
                                    'rootfs_layers': item['RootFS']['Layers']}
    archive = EVIDENCE / 'release-candidate-images-a01a0d7c.json'
    current = EVIDENCE / 'release-candidate-images.json'
    if not archive.exists():
        assert json.loads(current.read_text())['business_sha'].startswith('a01a0d7c')
        archive.write_bytes(current.read_bytes())
    target = EVIDENCE / 'release-candidate-images-220257a.json'
    assert not target.exists(), 'Retain previous manifest; do not overwrite candidate evidence'
    text = json.dumps(manifest, indent=2) + '\n'
    target.write_text(text); current.write_text(text)
    config = ROOT / '.codex-optimization/hardened.compose.json'
    document = json.loads(config.read_text())
    assert document['name'] == PROJECT and 'x-preparation-not-for-start' in document
    for name, service in document['services'].items():
        key = name if name in manifest['images'] else 'agent'
        service['image'] = manifest['images'][key]['id']
    assert 'command' not in document['services']['agent'], 'Actual image CMD required'
    assert document['services']['agent']['read_only'] is True
    assert document['services']['agent']['cap_drop'] == ['ALL']
    document['x-acceptance-boundary'] = document.pop('x-preparation-not-for-start')['topology_boundary']
    config.write_text(json.dumps(document, indent=2) + '\n')
    result = subprocess.run(['docker', 'compose', '-f', str(config), 'up', '-d', '--wait', '--wait-timeout', '180'],
                            cwd=ROOT, capture_output=True, text=True, timeout=240)
    private_log = ROOT / '.codex-optimization-hardened-start.log'
    private_log.write_text(result.stdout + '\n' + result.stderr)
    report = {'project': PROJECT, 'business_sha': REVISION, 'passed': result.returncode == 0,
              'real_agent_image_cmd': True, 'explicit_browser_provider_fixture': True,
              'private_log': str(private_log), 'daily_replaced': False, 'docker_deletion': False}
    (EVIDENCE / 'hardened-candidate-start.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
