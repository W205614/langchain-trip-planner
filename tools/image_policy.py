"""Explicit, expiring distribution assessments; never a whole-package ignore.

Inventory is copied from an exact immutable image using a stopped UUID-owned
container. No image command is executed. Local objects are retained by default;
explicit cleanup is available for the CI runner's own ephemeral environment.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import uuid


def load_policy(path, root, today=None):
    policy = json.loads(Path(path).read_text(encoding='utf-8'))
    today = today or datetime.now(timezone.utc).date()
    if date.fromisoformat(policy['expires_on']) < today:
        raise RuntimeError('Distribution assessment expired; retain raw findings and reassess')
    if not policy.get('assessments') or not policy.get('source_bindings'):
        raise RuntimeError('Distribution assessment is incomplete')
    for relative, digest in policy['source_bindings'].items():
        source = (root / relative).resolve()
        if not source.is_relative_to(root.resolve()):
            raise RuntimeError('Distribution source binding escapes repository')
        actual = hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if actual != digest:
            raise RuntimeError('Distribution source binding changed: ' + relative)
    for entry in policy['assessments']:
        if (entry.get('classification') not in ('fixed_by_distribution_backport', 'fixed_by_pinned_security_upgrade', 'still_vulnerable_vendor_minor', 'not_affected_component_absent')
                or not entry.get('ids') or not entry.get('artifact_versions') or not entry.get('requires_dpkg')
                or not entry.get('references') or not entry.get('reason')):
            raise RuntimeError('Distribution entry lacks exact versions, evidence or an honest classification')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('vendor_status') != 'vulnerable':
            raise RuntimeError('Vendor severity calibration must retain vulnerable status')
        if entry.get('artifact_type') not in ('deb', 'binary'):
            raise RuntimeError('Distribution assessment cannot suppress language dependencies')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('artifact_type') != 'deb':
            raise RuntimeError('Vendor severity calibration requires a distribution package')
        if entry['classification'] == 'still_vulnerable_vendor_minor' and entry.get('vendor_severity') != 'minor_no_dsa':
            raise RuntimeError('Vendor severity calibration requires explicit reviewed Minor/no-DSA evidence')
        if entry['classification'] == 'not_affected_component_absent' and not (entry.get('absent_paths') or entry.get('file_sha256')):
            raise RuntimeError('Component-absence assessment requires actual image file evidence')
        if entry['classification'] == 'fixed_by_pinned_security_upgrade' and not entry.get('file_sha256'):
            raise RuntimeError('Security package upgrade assessment requires the actual reviewed ELF hash')
    return policy


def dpkg_inventory(text):
    packages = {}
    for paragraph in text.split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        if fields.get('Status') == 'install ok installed' and fields.get('Package') and fields.get('Version'):
            packages[fields['Package']] = fields['Version']
    return packages


def inventory(image, policy, destination, cleanup_owned_resources=False):
    name = 'trip-security-inventory-' + uuid.uuid4().hex[:12]
    result = subprocess.run(['docker', 'create', '--name', name, '--entrypoint', '/bin/false', image],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Cannot create stopped inventory object')
    resource = {'name': name, 'container_id': result.stdout.strip(), 'image_id': image,
                'purpose': 'stopped image inventory extraction; no image command executed',
                'business_data': False, 'status': 'retained', 'cleanup_explicit': cleanup_owned_resources}
    resource_path = destination / (name + '-resource.json')
    resource_path.write_text(json.dumps(resource, indent=2) + '\n', encoding='utf-8')
    copied = {}
    absent = []
    # Debian /etc/os-release is a symlink; use its canonical regular file so
    # stopped-container export does not depend on Docker symlink traversal.
    paths = {'/var/lib/dpkg/status', '/usr/lib/os-release'}
    for entry in policy['assessments']:
        paths.update(entry.get('absent_paths', []))
        paths.update(entry.get('file_sha256', {}))
    try:
        for path in sorted(paths):
            if not path.startswith('/') or '..' in Path(path).parts:
                raise RuntimeError('Unsafe inventory path')
            output = subprocess.run(['docker', 'cp', '--follow-link', name + ':' + path, '-'], capture_output=True, timeout=30)
            if output.returncode:
                if b'Could not find the file' in output.stderr or b'No such file or directory' in output.stderr:
                    absent.append(path)
                    continue
                raise RuntimeError('Image inventory extraction failed')
            with tarfile.open(fileobj=io.BytesIO(output.stdout)) as archive:
                files = [member for member in archive.getmembers() if member.isfile()]
                if len(files) != 1:
                    raise RuntimeError('Inventory target is not exactly one regular file')
                copied[path] = archive.extractfile(files[0]).read()
    finally:
        if cleanup_owned_resources:
            cleaned = subprocess.run(['docker', 'rm', '--volumes', name], capture_output=True, timeout=30)
            resource['status'] = 'cleaned_explicit_ci' if cleaned.returncode == 0 else 'cleanup_failed'
            resource_path.write_text(json.dumps(resource, indent=2) + '\n', encoding='utf-8')
            if cleaned.returncode:
                raise RuntimeError('Exact stopped inventory object cleanup failed: ' + name)
    if '/var/lib/dpkg/status' not in copied or '/usr/lib/os-release' not in copied:
        raise RuntimeError('Distribution identity or dpkg inventory unavailable; missing=' + ','.join(absent))
    release = dict(line.split('=', 1) for line in copied['/usr/lib/os-release'].decode().splitlines() if '=' in line)
    environment_result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .Config.Env}}', image],
                                        capture_output=True, text=True, timeout=30, check=True)
    environment_names = [item.split('=', 1)[0] for item in json.loads(environment_result.stdout) or []]
    evidence = {'image_id': image, 'distribution': release.get('ID', '').strip('"'),
                'release': release.get('VERSION_ID', '').strip('"'),
                'packages': dpkg_inventory(copied['/var/lib/dpkg/status'].decode()),
                'environment_names': environment_names,
                'file_sha256': {path: hashlib.sha256(raw).hexdigest() for path, raw in copied.items()},
                'absent_paths': absent, 'resource_manifest': str(resource_path),
                'method': 'docker cp from exact stopped UUID-owned container; no image commands executed; retained locally by default'}
    (destination / 'distribution-inventory.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


def assess(policy, match, evidence):
    if not policy or not evidence:
        return None
    artifact, vulnerability = match.get('artifact', {}), match.get('vulnerability', {})
    for entry in policy['assessments']:
        if (evidence.get('distribution') != entry['distribution'] or evidence.get('release') != entry['release']
                or artifact.get('type') != entry['artifact_type']
                or entry['artifact_versions'].get(artifact.get('name')) != artifact.get('version')
                or vulnerability.get('id') not in entry['ids']):
            continue
        if any(evidence.get('packages', {}).get(name) != version for name, version in entry['requires_dpkg'].items()):
            continue
        if set(entry.get('requires_packages_absent', [])).intersection(evidence.get('packages', {})):
            continue
        if set(entry.get('requires_environment_absent', [])).intersection(evidence.get('environment_names', [])):
            continue
        if not set(entry.get('absent_paths', [])).issubset(evidence.get('absent_paths', [])):
            continue
        if any(evidence.get('file_sha256', {}).get(path) != digest for path, digest in entry.get('file_sha256', {}).items()):
            continue
        return entry['classification']
    return None
