"""Narrow, expiring human acceptance of still-vulnerable components, never a fix.

This module validates a frozen record. It never refreshes approval fingerprints.
Repository/policy editing authority remains the trust boundary; JSON is not a
cryptographic signature by the human. Runtime receipts must be freshly produced
by the reviewed probe and refer to an actual still-running exact-image container.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

APPROVED_REVISION = '220257ad32e50d08cb987d565882f8a038e36c6f'
APPROVED_IMAGE = 'sha256:f9a0a0deb768b350c504f0be04e6cdfcc8c1c3aca54f319feecbab0cc54f9ab8'
ORIGINAL_AUTHORIZATION_SHA256 = '1a7dcf73e06fd08b5b71f609fdc6817bdf6178c4671dc815eb5acedf3bf9f4e6'
PUBLIC_AUTHORIZATION_SHA256 = '2226575af1273d277c232dc3f609a648edecc852513bad0181758c95564b3a03'
MAX_EXPIRY = datetime.fromisoformat('2026-11-09T23:59:59+08:00')
ALLOWED_IDS = frozenset({'CVE-2026-102010', 'CVE-2026-19499', 'CVE-2026-5435',
                         'CVE-2026-76642', 'CVE-2026-78408', 'CVE-2026-78409',
                         'CVE-2026-78410', 'CVE-2026-95619'})
BUSINESS_DIRECTORIES = ('backend', 'business-backend', 'frontend', 'deploy', 'contracts')
APPLICATION_HASH_ALGORITHM = 'utf8-crlf-to-lf-v1'
TEXT_EXTENSIONS = frozenset({'.py', '.json', '.md', '.txt', '.lock', '.yml', '.yaml',
                              '.toml', '.ini', '.html', '.css', '.js', '.ts'})
REQUIRED_RUNTIME = {'user': '1000', 'cap_eff': '0000000000000000',
                    'cap_bnd': '0000000000000000', 'no_new_privileges': True,
                    'readonly_rootfs': True, 'privileged': False,
                    'cap_drop_all': True, 'restricted_tools_absent': True,
                    'actual_uvicorn': True}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def application_digest(path, raw):
    if Path(path).suffix.lower() in TEXT_EXTENSIONS:
        try:
            raw.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            pass
        else:
            raw = raw.replace(b'\r\n', b'\n')
    return digest(raw)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('timezone missing')
        return result
    except (ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError('Risk acceptance timestamp must include a timezone') from exc


def repository_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RuntimeError('Risk acceptance path must be repository-relative')
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()) or not result.is_file() or result.is_symlink():
        raise RuntimeError('Risk acceptance evidence path is unavailable or unsafe')
    return result


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Approved business git objects unavailable; fetch approved history, never rebaseline silently')
    return result.stdout.decode('utf-8').strip()


def collect_source_bindings(root, revision=None):
    """Read-only generation helper; calling it does not grant approval."""
    revision = revision or APPROVED_REVISION
    trees = {path: git(root, 'rev-parse', revision + ':' + path) for path in BUSINESS_DIRECTORIES}
    names = git(root, 'ls-tree', '--name-only', revision).splitlines()
    recipes = sorted(n for n in names if n.startswith('docker-compose') and n.endswith(('.yml', '.yaml')))
    if not {'docker-compose.yml', 'docker-compose.production.yml'}.issubset(recipes):
        raise RuntimeError('Approved Compose recipes missing')
    files = {}
    for path in recipes:
        result = subprocess.run(['git', 'show', revision + ':' + path], cwd=root, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Approved recipe unavailable')
        files[path] = digest(result.stdout.replace(b'\r\n', b'\n'))
    return {'git_tree_bindings': trees, 'source_bindings': files}


def validate_sources(approval, root, image_revision, source_mode=None):
    if approval.get('approved_business_revision') != APPROVED_REVISION:
        raise RuntimeError('Risk acceptance business revision outside human approval')
    head = git(root, 'rev-parse', 'HEAD')
    if image_revision not in (APPROVED_REVISION, head):
        raise RuntimeError('Image revision is neither approved anchor nor current policy-only build')
    expected = collect_source_bindings(root)
    if approval.get('git_tree_bindings') != expected['git_tree_bindings'] or approval.get('source_bindings') != expected['source_bindings']:
        raise RuntimeError('Frozen approval source/tree fingerprints differ from approved business git objects')
    current = collect_source_bindings(root, head)
    actual_recipes = {p.name for p in root.iterdir() if p.name.startswith('docker-compose') and p.suffix in ('.yml', '.yaml')}
    if current['source_bindings'] != expected['source_bindings'] or actual_recipes != set(expected['source_bindings']):
        raise RuntimeError('Compose recipe set or content changed since explicit approval')
    if source_mode is not None:
        try:
            from . import source_equivalence_delta
        except ImportError:
            import source_equivalence_delta
        identity = source_equivalence_delta.validate_release(root, source_mode)
        return {**identity, 'image_revision': image_revision}
    for path, oid in expected['git_tree_bindings'].items():
        if git(root, 'rev-parse', head + ':' + path) != oid:
            raise RuntimeError('Business source/lock/recipe tree changed: ' + path)
    paths = [*BUSINESS_DIRECTORIES, *expected['source_bindings']]
    if git(root, 'status', '--porcelain', '--untracked-files=all', '--', *paths):
        raise RuntimeError('Business source/recipe working tree is dirty; acceptance cannot cover uncommitted changes')
    for path, expected_hash in expected['source_bindings'].items():
        if digest(repository_path(root, path).read_bytes().replace(b'\r\n', b'\n')) != expected_hash:
            raise RuntimeError('Compose recipe changed: ' + path)
    return {'approved_business_revision': APPROVED_REVISION, 'current_policy_revision': head,
            'image_revision': image_revision, 'business_trees_equal': True}


def load(path, root, now=None):
    now = now or datetime.now(timezone.utc)
    approval = json.loads(Path(path).read_text(encoding='utf-8'))
    if approval.get('schema_version') != 1:
        raise RuntimeError('Unsupported explicit risk acceptance schema')
    expiry = timestamp(approval.get('expires_at'))
    if now > expiry or expiry > MAX_EXPIRY:
        raise RuntimeError('Explicit human risk acceptance expired or exceeds authorized deadline')
    if (set(approval.get('allowed_cve_ids', [])) != ALLOWED_IDS
            or len(approval.get('allowed_cve_ids', [])) != len(ALLOWED_IDS)):
        raise RuntimeError('Risk acceptance must cover exactly the eight human-reviewed IDs')
    if (approval.get('effective_scope') != 'controlled_single_host_local_demo_only'
            or approval.get('deployment_window_granted') is not False
            or approval.get('classification') != 'still_vulnerable_temporarily_risk_accepted_not_fixed'):
        raise RuntimeError('Risk acceptance cannot authorize public production or deployment')
    auth = approval.get('authorization', {})
    record_path = repository_path(root, auth.get('record_path'))
    if (digest(record_path.read_bytes()) != auth.get('record_sha256') or auth.get('record_sha256') != PUBLIC_AUTHORIZATION_SHA256
            or auth.get('original_record_sha256') != ORIGINAL_AUTHORIZATION_SHA256):
        raise RuntimeError('Human authorization identity/hash does not match reviewed original record')
    record = json.loads(record_path.read_text(encoding='utf-8'))
    if (record.get('accepted') is not True or not record.get('human_answer')
            or record.get('business_sha') != APPROVED_REVISION or record.get('reviewed_image_id') != APPROVED_IMAGE
            or set(record.get('still_vulnerable_ids', [])) != ALLOWED_IDS
            or record.get('deployment_window_granted') is not False or record.get('original_authorization_sha256') != ORIGINAL_AUTHORIZATION_SHA256
            or timestamp(record.get('policy', {}).get('expires')) != MAX_EXPIRY):
        raise RuntimeError('Public authorization copy does not preserve the exact human decision and scope')
    if now < timestamp(record.get('authorized_at')):
        raise RuntimeError('Risk acceptance is not effective yet')
    anchor = approval.get('approved_image', {})
    if (anchor.get('image_id') != APPROVED_IMAGE or anchor.get('distribution') != 'debian' or anchor.get('release') != '13'
            or not anchor.get('packages') or not anchor.get('native_elf_sha256') or not anchor.get('application_file_sha256')
            or anchor.get('application_hash_algorithm') != APPLICATION_HASH_ALGORITHM
            or approval.get('runtime_required') != REQUIRED_RUNTIME):
        raise RuntimeError('Risk acceptance lacks complete immutable component and runtime fingerprints')
    for key in ('native_elf_sha256', 'application_file_sha256'):
        if any(not k.startswith('/') or not re.fullmatch('[0-9a-f]{64}', v) for k, v in anchor[key].items()):
            raise RuntimeError('Malformed exact image file fingerprints')
    return approval


def validate_scan_receipt(data, image_id, started_at, now=None):
    """An old/partial scan or stale advisory DB cannot authorize residual risk."""
    now = now or datetime.now(timezone.utc)
    if (not isinstance(data, dict) or not isinstance(data.get('matches'), list)
            or data.get('source', {}).get('type') != 'image'
            or data.get('source', {}).get('target', {}).get('userInput') != image_id):
        raise RuntimeError('Risk acceptance scan is missing or does not identify the immutable scanned image')
    descriptor = data.get('descriptor', {})
    status = descriptor.get('db', {}).get('status', {})
    scanned, built = timestamp(descriptor.get('timestamp')), timestamp(status.get('built'))
    if (status.get('valid') is not True or now - built > timedelta(days=3) or built > now + timedelta(minutes=2)
            or scanned < started_at - timedelta(minutes=1) or scanned > now + timedelta(minutes=2)):
        raise RuntimeError('Risk acceptance scan/database receipt expired, invalid or reused')
    return {'scanner_report_time': descriptor['timestamp'], 'database_built': status['built'],
            'database_valid': True, 'maximum_database_age_hours': 72,
            'scanned_immutable_image': image_id, 'fresh_scan_verified': True}


def validate_components(approval, evidence):
    anchor = approval['approved_image']
    if not evidence.get('native_inventory_complete') or evidence.get('application_hash_algorithm') != APPLICATION_HASH_ALGORITHM:
        raise RuntimeError('Complete native/application image inventory missing')
    for key in ('distribution', 'release', 'packages', 'native_elf_sha256', 'application_file_sha256'):
        if evidence.get(key) != anchor.get(key):
            raise RuntimeError('Accepted image component set changed: ' + key)


def validate_runtime(approval, proof, image_id, root, now=None):
    now = now or datetime.now(timezone.utc)
    checked = timestamp(proof.get('checked_at'))
    if checked > now + timedelta(minutes=2) or now - checked > timedelta(hours=24):
        raise RuntimeError('Runtime constraint proof stale or from the future')
    if (proof.get('schema_version') != 1 or proof.get('passed') is not True
            or proof.get('image_id') != image_id or proof.get('constraints') != approval['runtime_required']
            or not re.fullmatch('[0-9a-f]{64}', proof.get('container_id', ''))):
        raise RuntimeError('Actual runtime combination is not the approved constraint combination')
    probe = repository_path(root, 'tools/runtime_constraint_probe.py')
    if proof.get('probe_source_sha256') != digest(probe.read_bytes().replace(b'\r\n', b'\n')):
        raise RuntimeError('Runtime proof does not identify the current reviewed probe')
    try:
        from .runtime_constraint_probe import collect_existing
    except ImportError:
        from runtime_constraint_probe import collect_existing
    fresh = collect_existing(proof['container_id'])
    if any(fresh.get(key) != proof.get(key) for key in ('schema_version', 'passed', 'source_revision',
                                                      'image_id', 'container_id', 'constraints', 'probe_source_sha256')):
        raise RuntimeError('Live runtime recheck does not match the supplied reviewed constraint receipt')
    result = subprocess.run(['docker', 'inspect', proof['container_id']], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Runtime proof container no longer exists')
    observed = json.loads(result.stdout)[0]
    cfg, host, state = observed.get('Config', {}), observed.get('HostConfig', {}), observed.get('State', {})
    if (observed.get('Id') != proof['container_id'] or observed.get('Image') != image_id or state.get('Running') is not True
            or state.get('Restarting') or timestamp(state.get('StartedAt')) > checked
            or host.get('Privileged') is not False or host.get('ReadonlyRootfs') is not True
            or host.get('CapAdd') or set(host.get('CapDrop') or []) != {'ALL'}
            or not any(s in ('no-new-privileges', 'no-new-privileges:true') for s in host.get('SecurityOpt') or [])
            or cfg.get('User') not in ('appuser', '1000', '1000:1000')):
        raise RuntimeError('Live immutable container context differs from runtime receipt')
    return {'container_id': proof['container_id'], 'checked_at': proof['checked_at'],
            'probe_source_sha256': proof['probe_source_sha256'], 'live_container_identity_verified': True}


def verify(approval, evidence, proof, root, image_revision, now=None, source_mode=None):
    now = now or datetime.now(timezone.utc)
    if now > timestamp(approval['expires_at']):
        raise RuntimeError('Human acceptance expired while scan was running')
    identity = validate_sources(approval, root, image_revision, source_mode=source_mode)
    validate_components(approval, evidence)
    runtime = validate_runtime(approval, proof, evidence['image_id'], root, now)
    if proof.get('source_revision') != image_revision:
        raise RuntimeError('Runtime receipt revision does not identify the scanned image')
    return {'status': 'effective_temporary_acceptance_still_vulnerable',
            'effective_scope': approval['effective_scope'], 'expires_at': approval['expires_at'],
            'allowed_cve_ids': sorted(ALLOWED_IDS), 'authorization': approval['authorization'],
            'identity_proof': identity, 'runtime_proof': runtime, 'components_exactly_equal': True,
            'deployment_window_granted': False, 'classification': 'still_vulnerable_not_fixed'}
