"""Exact source/trace-bound false-positive review, not vulnerability acceptance.

No query-, directory- or severity-wide suppression and no automatic fingerprint
refresh. Missing, changed or expired evidence fails closed. Raw findings remain.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

BUSINESS_DIRECTORIES = ('backend', 'business-backend', 'frontend', 'deploy', 'contracts')
MAX_EXPIRY = datetime.fromisoformat('2026-11-09T23:59:59+08:00')
FLOW_ALGORITHM = 'sarif-semantic-flow-v1'
EXPECTED_ENTRY_COUNT = 22


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def repository_path(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in PurePosixPath(relative).parts:
        raise RuntimeError('Unsafe CodeQL contextual source path')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise RuntimeError('CodeQL contextual source/evidence unavailable')
    return path


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('CodeQL reviewed business git objects unavailable; no automatic rebaseline')
    return result.stdout.decode('utf-8').strip()


def artifact_identity(value, run):
    if not isinstance(value, dict):
        raise RuntimeError('Invalid CodeQL trace artifact reference')
    location = dict(value)
    if 'index' in location:
        index, artifacts = location['index'], run.get('artifacts', [])
        if type(index) is not int or not isinstance(artifacts, list) or not 0 <= index < len(artifacts):
            raise RuntimeError('CodeQL trace artifact identity unavailable')
        declared = artifacts[index].get('location', {})
        for key in ('uri', 'uriBaseId'):
            if key in location and key in declared and location[key] != declared[key]:
                raise RuntimeError('CodeQL trace artifact URI/index disagree')
        location = {**declared, **location}
    if not isinstance(location.get('uri'), str):
        raise RuntimeError('CodeQL trace artifact URI missing')
    location.pop('index', None)
    return location


def semantic_flow_hash(result, run):
    """Bind flow meaning, resolving file identities instead of volatile indexes.

Keep each thread's step order, regions, messages and dataflow-role metadata.
Outer flow/thread order and related-location numeric labels are not semantics.
Artifact indexes are resolved to their declared URI, never silently discarded.
"""
    def message(value):
        if not isinstance(value, dict):
            return {}
        return {k: value[k] for k in ('text', 'markdown', 'arguments') if k in value}

    def location(value):
        if not isinstance(value, dict):
            raise RuntimeError('CodeQL semantic flow location unavailable')
        output = {}
        if 'physicalLocation' in value:
            physical = value['physicalLocation']
            artifact = artifact_identity(physical.get('artifactLocation', {}), run)
            # CodeQL's declared SRCROOT-relative URI is the source identity.
            # Other unresolved bases/absolute references require new review.
            if artifact.get('uriBaseId') not in (None, '%SRCROOT%') or artifact['uri'].startswith(('/', 'file:', 'http:', 'https:')):
                raise RuntimeError('Unresolved CodeQL semantic source root')
            output['physicalLocation'] = {'uri': artifact['uri']}
            for key in ('region', 'contextRegion'):
                if key in physical:
                    region = physical[key]
                    output['physicalLocation'][key] = {k: region[k] for k in (
                        'startLine', 'startColumn', 'endLine', 'endColumn', 'charOffset', 'charLength',
                        'byteOffset', 'byteLength') if k in region}
                    if 'snippet' in region:
                        output['physicalLocation'][key]['snippet'] = message(region['snippet'])
        if 'message' in value:
            output['message'] = message(value['message'])
        if 'logicalLocations' in value:
            logical = []
            for item in value['logicalLocations']:
                identity = {k: item[k] for k in ('name', 'fullyQualifiedName', 'decoratedName', 'kind') if k in item}
                if not identity:
                    raise RuntimeError('Opaque CodeQL logical location cannot establish flow identity')
                logical.append(identity)
            output['logicalLocations'] = logical
        return output

    if result.get('graphTraversals'):
        raise RuntimeError('Additional CodeQL graph semantics require explicit review')
    flows = []
    for flow in result.get('codeFlows', []):
        threads = []
        for thread in flow.get('threadFlows', []):
            points = []
            for point in thread.get('locations', []):
                points.append({'location': location(point.get('location')),
                               **{k: point[k] for k in ('state', 'importance', 'nestingLevel', 'taxa') if k in point}})
            threads.append({'locations': points, **{k: thread[k] for k in ('initialState', 'immutableState') if k in thread}})
        flows.append({'message': message(flow.get('message')), 'threadFlows': sorted(threads, key=canonical)})
    related = sorted([location(v) for v in result.get('relatedLocations', [])], key=canonical)
    return digest(canonical({'algorithm': FLOW_ALGORITHM, 'codeFlows': sorted(flows, key=canonical), 'relatedLocations': related}))


def finding_identity(rule, result, run):
    locations = result.get('locations', [])
    if not isinstance(locations, list) or len(locations) != 1:
        return None  # An additional/changed primary location needs new review.
    physical = locations[0].get('physicalLocation', {})
    path = artifact_identity(physical.get('artifactLocation', {}), run)['uri']
    if not isinstance(path, str) or path.startswith('/') or '..' in PurePosixPath(path).parts:
        return None
    return {'query': result.get('ruleId', result.get('rule', {}).get('id', rule['id'])),
            'path': path, 'region': physical.get('region'),
            'security_severity': rule.get('properties', {}).get('security-severity'),
            'semantic_flow_sha256': semantic_flow_hash(result, run)}


def load(path, root, now=None, source_mode=None):
    now = now or datetime.now(timezone.utc)
    policy = json.loads(Path(path).read_text(encoding='utf-8'))
    try:
        expiry = datetime.fromisoformat(policy['expires_at'])
        if expiry.tzinfo is None or now > expiry or expiry > MAX_EXPIRY:
            raise ValueError('expiry')
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('CodeQL contextual review expired or invalid') from error
    if (policy.get('schema_version') != 1 or policy.get('flow_hash_algorithm') != FLOW_ALGORITHM
            or policy.get('classification') != 'not_affected_context'
            or not isinstance(policy.get('entries'), list) or len(policy['entries']) != EXPECTED_ENTRY_COUNT
            or set(policy.get('business_tree_bindings', {})) != set(BUSINESS_DIRECTORIES)
            or not policy.get('source_bindings')):
        raise RuntimeError('CodeQL contextual assessment incomplete; exact source/trace proof required')
    review_path = repository_path(root, policy.get('review_record'))
    if digest(review_path.read_bytes()) != policy.get('review_record_sha256'):
        raise RuntimeError('Independent CodeQL review evidence changed')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    head = git(root, 'rev-parse', 'HEAD')
    revision = policy['reviewed_business_revision']
    source_identity = None
    if source_mode is not None:
        try:
            from . import source_equivalence_delta
        except ImportError:
            import source_equivalence_delta
        source_identity = source_equivalence_delta.validate_release(root, source_mode, now)
        if source_identity['current_policy_revision'] != head:
            raise RuntimeError('CodeQL delta proof does not bind actual HEAD')
    for name, oid in policy['business_tree_bindings'].items():
        if (git(root, 'rev-parse', revision + ':' + name) != oid
                or (source_identity is None and git(root, 'rev-parse', head + ':' + name) != oid)
                or git(root, 'rev-parse', review['sarif_source_revision'] + ':' + name) != oid):
            raise RuntimeError('CodeQL business source tree changed: ' + name)
    if git(root, 'status', '--porcelain', '--untracked-files=all', '--', *BUSINESS_DIRECTORIES):
        raise RuntimeError('CodeQL business source working tree changed')
    for relative, expected in policy['source_bindings'].items():
        source = repository_path(root, relative)
        if (relative.split('/')[0] not in BUSINESS_DIRECTORIES
                or expected != review.get('supporting_source_bindings_lf', {}).get(relative)
                or digest(source.read_bytes().replace(b'\r\n', b'\n')) != expected):
            raise RuntimeError('CodeQL contextual source fingerprint changed: ' + relative)
    seen = set()
    for entry in policy['entries']:
        key = canonical({k: entry.get(k) for k in ('query', 'path', 'region', 'semantic_flow_sha256')})
        reviewed = [r for r in review.get('findings', []) if all(r.get(k) == entry.get(k) for k in ('query', 'path', 'region'))]
        if (key in seen or entry.get('classification') != 'not_affected_context' or not entry.get('reason')
                or not entry.get('semantic_flow_sha256') or not entry.get('security_severity')
                or entry.get('source_sha256') != policy['source_bindings'].get(entry.get('path'))
                or len(reviewed) != 1 or reviewed[0]['raw_trace_sha256'] != entry.get('reviewed_raw_trace_sha256')
                or reviewed[0]['source_sha256_current_worktree_lf'] != entry.get('source_sha256')):
            raise RuntimeError('CodeQL entry lacks exact independent source/trace review')
        seen.add(key)
    return {**policy, 'source_equivalence_identity': source_identity} if source_identity is not None else policy


def assess(policy, rule, result, run, now=None):
    if not policy:
        return None
    now = now or datetime.now(timezone.utc)
    if now > datetime.fromisoformat(policy['expires_at']):
        raise RuntimeError('CodeQL contextual review expired during analysis')
    identity = finding_identity(rule, result, run)
    if not identity:
        return None
    entries = [entry for entry in policy['entries'] if all(entry.get(k) == v for k, v in identity.items())]
    if len(entries) != 1:
        return None
    entry = entries[0]
    return {**identity, 'source_sha256': entry['source_sha256'], 'classification': 'not_affected_context',
            'reason': entry['reason'], 'expires_at': policy['expires_at'],
            'boundary': 'Exact source-reviewed analyzer context; raw severity preserved. Not the eight-CVE temporary risk acceptance.'}
