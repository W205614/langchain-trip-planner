"""Disabled, review-only preflight for one exact restore response correction.

This is not a new CVE acceptance, a CodeQL suppression, or an active CI gate.
It never substitutes revisions in the existing validators. Activation requires
separate review; every output from this module remains prepared_not_activated.
Fresh image inventory, runtime proof, scanner receipts and final-SHA CI remain
mandatory and are not established by a source-only preflight.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

try:
    from . import codeql_context as context, risk_acceptance as risk, sarif_rules, source_equivalence_activation as activation
except ImportError:
    import codeql_context as context
    import risk_acceptance as risk
    import sarif_rules
    import source_equivalence_activation as activation

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = '.github/source-equivalence/restore-response-v1.json'
ACTIVATED = False  # No environment variable or CLI flag can activate acceptance.


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Required frozen Git objects or source observation unavailable')
    return result.stdout


def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def safe_file(root, relative):
    path = root / relative
    if (Path(relative).is_absolute() or '..' in Path(relative).parts
            or not path.resolve().is_relative_to(root.resolve()) or not path.is_file()):
        raise RuntimeError('Unavailable/unsafe source-equivalence path: ' + relative)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or getattr(parent.stat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Source-equivalence path traverses a link/reparse point')
    return path


def observe_sources(root, manifest):
    _, plan = activation.preparation(root)
    paths = [*risk.BUSINESS_DIRECTORIES, *manifest['compose_paths']]
    trees = {revision: {name: git(root, 'rev-parse', revision + ':' + name).decode().strip()
                        for name in risk.BUSINESS_DIRECTORIES}
             for revision in manifest['historical_revisions']}
    raw = git(root, 'diff', '--raw', '-z', '--no-abbrev', '--no-renames',
              manifest['baseline_revision'], '--', *paths).split(b'\0')
    changed = {}
    for index in range(0, len(raw) - 1, 2):
        header, relative = raw[index].decode().split(), raw[index + 1].decode('utf-8')
        old_mode, new_mode, old_oid, _, status = header
        actual = safe_file(root, relative).read_bytes() if status == 'M' else b''
        # The only permitted modifications are the two reviewed UTF-8 Java files.
        # All other changes are rejected by the path/status comparison below.
        if relative in manifest['files']:
            actual.decode('utf-8', errors='strict')
            actual = actual.replace(b'\r\n', b'\n')
        changed[relative] = {'old_mode': old_mode.removeprefix(':'), 'new_mode': new_mode,
                             'old_blob': old_oid, 'new_blob': blob(actual), 'status': status}
    frozen = {}
    for relative, specification in manifest['frozen_files'].items():
        # Preserve old validator bindings as historical evidence. Their prepared
        # hooks have separate exact hashes, checked by the pinned preparation plan.
        raw_bytes = (git(root, 'show', manifest['baseline_revision'] + ':' + relative)
                     if relative in plan['integration_file_sha256_lf']
                     else safe_file(root, relative).read_bytes())
        if specification['algorithm'] == 'utf8-crlf-to-lf-v1':
            raw_bytes.decode('utf-8', errors='strict')
            raw_bytes = raw_bytes.replace(b'\r\n', b'\n')
        frozen[relative] = hashlib.sha256(raw_bytes).hexdigest()
    return {'historical_trees': trees, 'changed': changed,
            'untracked': git(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', *paths).decode('utf-8').split('\0')[:-1],
            'compose_paths': sorted(p.name for p in root.iterdir()
                                    if p.name.startswith('docker-compose') and p.suffix in ('.yml', '.yaml')),
            'frozen_files': frozen}


def validate_release_observation(manifest, observation):
    expected_trees = dict(manifest['original_business_trees'], **{'business-backend': manifest['proposed_business_tree']})
    if (not observation['baseline_is_ancestor'] or observation['candidate_trees'] != expected_trees
            or observation['compose_paths'] != manifest['compose_paths']
            or observation['committed_compose_paths'] != manifest['compose_paths']
            or observation['committed_changes'] != manifest['files']):
        raise RuntimeError('Real HEAD is not the exact reviewed descendant/two-file committed delta')
    if (observation['status'] or observation['head_entries'] != observation['index_entries']
            or observation['head_worktree_blobs'] != observation['actual_worktree_blobs']):
        raise RuntimeError('HEAD/index/working scope differs; restored working bytes cannot hide committed drift')


def observe_release(root, manifest, plan):
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if git(root, 'cat-file', '-t', head).strip() != b'commit':
        raise RuntimeError('Actual HEAD is not a commit')
    paths = [*risk.BUSINESS_DIRECTORIES, *manifest['compose_paths'], *manifest['frozen_files'],
             *plan['integration_file_sha256_lf'], activation.SCOPE_PATH, activation.REVIEW_PATH,
             activation.PLAN_PATH, activation.ACTIVATION_PATH, 'tools/source_equivalence_activation.py']
    paths = sorted(set(paths))
    committed = {}
    raw = git(root, 'diff', '--raw', '-z', '--no-abbrev', '--no-renames', manifest['baseline_revision'],
              head, '--', *risk.BUSINESS_DIRECTORIES, *manifest['compose_paths']).split(b'\0')
    for index in range(0, len(raw) - 1, 2):
        old_mode, new_mode, old_oid, new_oid, status = raw[index].decode().split()
        committed[raw[index + 1].decode('utf-8')] = {'old_mode': old_mode.removeprefix(':'), 'new_mode': new_mode,
                                                  'old_blob': old_oid, 'new_blob': new_oid, 'status': status}
    head_entries = {}
    for item in git(root, 'ls-tree', '-r', '-z', head, '--', *paths).split(b'\0'):
        if item:
            metadata, name = item.split(b'\t', 1); mode, kind, oid = metadata.decode().split()
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise RuntimeError('Unsupported link/submodule/mode in release source scope')
            head_entries[name.decode('utf-8')] = [mode, oid]
    index_entries = {}
    for item in git(root, 'ls-files', '--stage', '-z', '--', *paths).split(b'\0'):
        if item:
            metadata, name = item.split(b'\t', 1); mode, oid, stage = metadata.decode().split()
            if stage != '0':
                raise RuntimeError('Unmerged release source index')
            index_entries[name.decode('utf-8')] = [mode, oid]
    attributes = subprocess.run(['git', 'check-attr', '-z', '--stdin', 'text'], cwd=root,
                                input=b'\0'.join(p.encode('utf-8') for p in head_entries) + b'\0',
                                capture_output=True, timeout=30)
    if attributes.returncode:
        raise RuntimeError('Source checkout attributes unavailable')
    values = attributes.stdout.split(b'\0'); text_attributes = {
        values[i].decode('utf-8'): values[i + 2].decode() for i in range(0, len(values) - 1, 3)}
    actual_blobs = {}
    for relative, (mode, expected) in head_entries.items():
        path = safe_file(root, relative)
        if os.name != 'nt' and bool(path.stat().st_mode & 0o111) != (mode == '100755'):
            raise RuntimeError('Actual source executable mode differs from committed mode')
        raw_bytes = path.read_bytes()
        actual = blob(raw_bytes)
        if actual != expected and text_attributes.get(relative) != 'unset' and b'\0' not in raw_bytes:
            try:
                raw_bytes.decode('utf-8', errors='strict')
            except UnicodeDecodeError:
                pass
            else:
                actual = blob(raw_bytes.replace(b'\r\n', b'\n'))
        actual_blobs[relative] = actual
    ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', manifest['baseline_revision'], head],
                              cwd=root, capture_output=True, timeout=30)
    if ancestor.returncode not in (0, 1):
        raise RuntimeError('Actual baseline ancestry unavailable')
    return {'head': head, 'baseline_is_ancestor': ancestor.returncode == 0,
            'compose_paths': sorted(p.name for p in root.iterdir() if p.name.startswith('docker-compose') and p.suffix in ('.yml', '.yaml')),
            'committed_compose_paths': sorted(p for p in git(root, 'ls-tree', '--name-only', head).decode('utf-8').splitlines()
                                             if p.startswith('docker-compose') and p.endswith(('.yml', '.yaml'))),
            'candidate_trees': {p: git(root, 'rev-parse', head + ':' + p).decode().strip() for p in risk.BUSINESS_DIRECTORIES},
            'committed_changes': committed, 'head_entries': head_entries, 'index_entries': index_entries,
            'head_worktree_blobs': {p: value[1] for p, value in head_entries.items()},
            'actual_worktree_blobs': actual_blobs,
            'status': git(root, 'status', '--porcelain', '--untracked-files=all', '--', *paths).decode()}


def validate_release(root, mode, now=None):
    plan = activation.require(root, mode, now)
    manifest, _ = activation.preparation(root)
    observed = observe_release(root, manifest, plan)
    validate_release_observation(manifest, observed)
    # Check immutable records in the committed artifact too, not only a restored
    # working copy. The index/worktree comparison above binds all three layers.
    for relative, specification in manifest['frozen_files'].items():
        if relative in plan['integration_file_sha256_lf']:
            continue
        raw = git(root, 'show', observed['head'] + ':' + relative)
        if specification['algorithm'] == 'utf8-crlf-to-lf-v1':
            raw = raw.replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != specification['sha256']:
            raise RuntimeError('Committed original risk/context record changed: ' + relative)
    for relative in [*plan['integration_file_sha256_lf'], activation.SCOPE_PATH, activation.REVIEW_PATH,
                     activation.PLAN_PATH, activation.ACTIVATION_PATH, 'tools/source_equivalence_activation.py']:
        if relative not in observed['head_entries']:
            raise RuntimeError('Release scope evidence/hook is not committed: ' + relative)
    return {'mode': activation.MODE, 'current_policy_revision': observed['head'],
            'baseline_revision': manifest['baseline_revision'], 'approved_business_revision': risk.APPROVED_REVISION,
            'business_trees_equal': False, 'exact_reviewed_java_delta': True,
            'agent_input_tree_equal': True, 'HEAD_index_worktree_equal': True,
            'original_risk_and_context_records_preserved': True, 'expires_at': manifest['expires_at']}


LANGUAGE_CONTEXTS = {'java-kotlin': ('java/', 18), 'python': ('py/', 2),
                     'javascript-typescript': ('js/', 2), 'actions': ('__no_reviewed_actions_query__/', 0)}


def codeql_environment(plan, head, language):
    if language not in LANGUAGE_CONTEXTS:
        raise RuntimeError('Unsupported fixed CodeQL matrix language')
    identity = {key: os.environ.get(key) for key in ('GITHUB_SHA', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID',
                'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB', 'GITHUB_WORKFLOW_REF')}
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or identity['GITHUB_SHA'] != head
            or identity['GITHUB_REPOSITORY'] != plan['repository'] or identity['GITHUB_JOB'] != 'codeql'
            or not re.fullmatch(r'[1-9][0-9]*', identity['GITHUB_RUN_ID'] or '')
            or not re.fullmatch(r'[1-9][0-9]*', identity['GITHUB_RUN_ATTEMPT'] or '')
            or not (identity['GITHUB_WORKFLOW_REF'] or '').startswith(plan['repository'] + '/.github/workflows/security.yml@')
            or os.environ.get('TRIP_CODEQL_ANALYZE_OUTCOME') != 'success'):
        raise RuntimeError('Current official CodeQL job/HEAD/analysis-success association unavailable')
    return {**identity, 'language': language, 'category': '/language:' + language + '/'}


def codeql_files(directory):
    directory = Path(directory).resolve()
    files = sorted(directory.rglob('*.sarif'))
    if not files or any(p.is_symlink() or not p.resolve().is_relative_to(directory) for p in files):
        raise RuntimeError('Complete unchanged job-owned raw CodeQL SARIF unavailable')
    return files, {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def codeql_language_context(policy, documents, language, now):
    if not documents:
        raise RuntimeError('Current successful CodeQL documents unavailable, including zero-finding languages')
    prefix, count = LANGUAGE_CONTEXTS[language]
    subset = {**policy, 'entries': [entry for entry in policy['entries'] if entry['query'].startswith(prefix)]}
    if len(subset['entries']) != count:
        raise RuntimeError('Original fixed CodeQL language context subset changed')
    for document in documents:
        runs = document.get('runs')
        if not isinstance(runs, list) or not runs:
            raise RuntimeError('Current successful CodeQL run unavailable')
        for run in runs:
            invocations = run.get('invocations')
            if (run.get('automationDetails', {}).get('id') != '/language:' + language + '/'
                    or not isinstance(invocations, list) or not invocations
                    or any(item.get('executionSuccessful') is not True for item in invocations)):
                raise RuntimeError('CodeQL category or complete successful invocation missing/mismatched')
    return validate_sarif_runs(subset, documents, now=now)


def write_codeql_receipt(root, directory, language, mode, now=None):
    """Trusted pinned workflow association, never a cryptographic scan signature."""
    now = now or datetime.now(timezone.utc)
    source = validate_release(root, mode, now)
    _, plan = activation.preparation(root)
    identity = codeql_environment(plan, source['current_policy_revision'], language)
    files, hashes = codeql_files(directory)
    policy = json.loads(safe_file(root, '.github/codeql-context-assessments.json').read_text(encoding='utf-8'))
    count = codeql_language_context(policy, [json.loads(p.read_bytes()) for p in files], language, now)
    receipt = {'schema_version': 1, 'checked_at': now.isoformat(), 'job_identity': identity,
               'raw_sarif_sha256': hashes, 'producer_sha256_lf': plan['integration_file_sha256_lf']['tools/source_equivalence_delta.py'],
               'workflow_sha256_lf': plan['integration_file_sha256_lf']['.github/workflows/security.yml'],
               'scope_complete_for_language': True, 'matched_language_context_count': count,
               'whole_22_verified': False, 'trust_boundary': 'Pinned same-job workflow association; not independent cryptographic provenance'}
    with (Path(directory) / 'source-equivalence-job-receipt.json').open('x', encoding='utf-8', newline='\n') as output:
        json.dump(receipt, output, indent=2); output.write('\n')
    return receipt


def verify_codeql_job(root, directory, policy, mode, language, now=None):
    now = now or datetime.now(timezone.utc)
    source = validate_release(root, mode, now)
    _, plan = activation.preparation(root)
    identity = codeql_environment(plan, source['current_policy_revision'], language)
    files, hashes = codeql_files(directory)
    receipt = json.loads((Path(directory) / 'source-equivalence-job-receipt.json').read_bytes())
    validate_codeql_receipt(receipt, identity, hashes, plan, now)
    count = codeql_language_context(policy, [json.loads(p.read_bytes()) for p in files], language, now)
    if type(receipt.get('matched_language_context_count')) is not int or receipt['matched_language_context_count'] != count:
        raise RuntimeError('CodeQL language context count mismatch')
    return {'source_identity': source, 'job_identity': identity, 'scope_complete_for_language': True,
            'matched_language_context_count': count, 'whole_22_verified': False}


def validate_codeql_receipt(receipt, identity, hashes, plan, now):
    checked = risk.timestamp(receipt.get('checked_at'))
    if (receipt.get('schema_version') != 1 or receipt.get('job_identity') != identity
            or receipt.get('raw_sarif_sha256') != hashes or checked > now or (now - checked).total_seconds() > 1800
            or receipt.get('producer_sha256_lf') != plan['integration_file_sha256_lf']['tools/source_equivalence_delta.py']
            or receipt.get('workflow_sha256_lf') != plan['integration_file_sha256_lf']['.github/workflows/security.yml']
            or receipt.get('scope_complete_for_language') is not True or receipt.get('whole_22_verified') is not False):
        raise RuntimeError('Current CodeQL job receipt mismatched, stale or raw files changed')


def validate_observation(manifest, observed):
    if observed['historical_trees'] != {r: manifest['original_business_trees'] for r in manifest['historical_revisions']}:
        raise RuntimeError('Historical business trees differ; no source rebaseline permitted')
    if (observed['untracked'] or observed['compose_paths'] != manifest['compose_paths']
            or observed['changed'] != manifest['files']):
        raise RuntimeError('Candidate is not the exact two-path/mode/blob delta; extra changes are blocked')
    if (set(observed['frozen_files']) != set(manifest['frozen_files'])
            or any(value not in specification.get('exact_checkout_sha256', [specification['sha256']])
                   for relative, specification in manifest['frozen_files'].items()
                   for value in [observed['frozen_files'][relative]])):
        raise RuntimeError('Original policy/review/validator bytes changed')


def projected_business_tree(root, manifest):
    """Compute Git tree identity in memory only; never write objects or index."""
    def project(directory):
        raw_tree = bytearray()
        for line in git(root, 'ls-tree', '-z', manifest['baseline_revision'] + ':' + directory).split(b'\0'):
            if not line:
                continue
            metadata, name = line.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            relative = directory + '/' + name.decode('utf-8')
            if relative in manifest['files']:
                oid = manifest['files'][relative]['new_blob']
            elif kind == 'tree' and any(p.startswith(relative + '/') for p in manifest['files']):
                oid = project(relative)
            raw_tree.extend(mode.lstrip('0').encode() + b' ' + name + b'\0' + bytes.fromhex(oid))
        return hashlib.sha1(b'tree ' + str(len(raw_tree)).encode() + b'\0' + raw_tree).hexdigest()
    return project('business-backend')


def check_expiry(manifest, policy, now):
    deadline = risk.timestamp(manifest['expires_at'])
    if (now > deadline or deadline != risk.MAX_EXPIRY
            or risk.timestamp(policy['expires_at']) != deadline):
        raise RuntimeError('Delta/original review expired or expiry differs from original authorization')


def validate_agent_evidence(approval, inventory, constraints):
    """Pure complete-map check only; does not replace fresh live runtime proof."""
    if inventory.get('native_inventory_complete') is not True:
        raise RuntimeError('Complete image inventory required')
    risk.validate_components(approval, inventory)
    if canonical(constraints) != canonical(approval['runtime_required']):
        raise RuntimeError('Runtime constraint set/type/value differs from original authorization')


def validate_sarif_runs(policy, documents, now=None):
    """Inspect every raw result, including lowered severities; never filter one."""
    identities = set()
    for document in documents:
        runs = document.get('runs')
        if document.get('version') != '2.1.0' or not isinstance(runs, list) or not runs:
            raise RuntimeError('Complete SARIF 2.1.0 run required')
        for run in runs:
            if run.get('tool', {}).get('driver', {}).get('name') != 'CodeQL' or not isinstance(run.get('results'), list):
                raise RuntimeError('Raw CodeQL results unavailable')
            if any(invocation.get('executionSuccessful') is False for invocation in run.get('invocations', [])):
                raise RuntimeError('CodeQL invocation failed')
            for result in run['results']:
                if result.get('suppressions') or result.get('baselineState') == 'absent':
                    raise RuntimeError('Suppressed/absent result cannot establish raw context equivalence')
                rule = sarif_rules.resolve_rule(run['tool'], result)
                assessed = context.assess(policy, rule, result, run, now=now)
                if not assessed:
                    raise RuntimeError('New or changed CodeQL query/location/flow/severity requires review')
                identity = canonical({k: assessed[k] for k in ('query', 'path', 'region', 'security_severity', 'semantic_flow_sha256')})
                if identity in identities:
                    raise RuntimeError('Duplicate raw CodeQL finding')
                identities.add(identity)
    expected = {canonical({k: entry[k] for k in ('query', 'path', 'region', 'security_severity', 'semantic_flow_sha256')})
                for entry in policy['entries']}
    if identities != expected or len(identities) != len(policy['entries']):
        raise RuntimeError('Raw CodeQL context set is missing, duplicated or changed')
    return len(identities)


def review_draft(root=ROOT, *, review_only=False, now=None, inventory=None, constraints=None, sarif_directory=None):
    if not review_only:
        raise RuntimeError('Source-equivalence delta is disabled; only explicit draft review is available')
    now = now or datetime.now(timezone.utc)
    manifest = json.loads(safe_file(root, MANIFEST).read_text(encoding='utf-8'))
    if ACTIVATED or manifest.get('enabled') is not False or manifest.get('schema_version') != 1:
        raise RuntimeError('Activation/schema change requires independent review; this tool cannot activate acceptance')
    observed = observe_sources(root, manifest)
    validate_observation(manifest, observed)
    projected = projected_business_tree(root, manifest)
    if projected != manifest['proposed_business_tree']:
        raise RuntimeError('Exact projected Java business tree mismatch')
    approval = risk.load(root / '.github/security-risk-acceptance.json', root, now)
    policy = json.loads(safe_file(root, '.github/codeql-context-assessments.json').read_text(encoding='utf-8'))
    check_expiry(manifest, policy, now)
    if (len(approval['approved_image']['application_file_sha256']) != 135
            or len(approval['approved_image']['packages']) != 87
            or len(approval['approved_image']['native_elf_sha256']) != 1086):
        raise RuntimeError('Original complete Agent component maps unavailable')
    for relative, expected in policy['source_bindings'].items():
        if hashlib.sha256(safe_file(root, relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise RuntimeError('CodeQL supporting source changed: ' + relative)
    if (inventory is None) != (constraints is None):
        raise RuntimeError('Inventory and runtime constraints must both be supplied for comparison')
    if inventory is not None:
        validate_agent_evidence(approval, inventory, constraints)
    sarif_count = None
    if sarif_directory is not None:
        files = sorted(Path(sarif_directory).rglob('*.sarif'))
        if not files:
            raise RuntimeError('Raw SARIF files unavailable')
        sarif_count = validate_sarif_runs(policy, [json.loads(p.read_text(encoding='utf-8')) for p in files], now=now)
    return {'status': 'prepared_not_activated', 'effective_security_acceptance': False,
            'source_mode': 'working_tree_preview_not_final_commit', 'projected_business_tree': projected,
            'exact_changed_files': list(manifest['files']), 'original_records_unchanged': True,
            'prepared_validator_hooks': ['tools/risk_acceptance.py', 'tools/codeql_context.py', 'tools/security_gate.py'],
            'historical_validator_bindings_preserved': True,
            'agent_component_comparison': 'equal_not_live_proof' if inventory is not None else 'pending_fresh_inventory_and_runtime',
            'sarif_context_comparison_count': sarif_count, 'sarif_revision_provenance_verified': False,
            'required_before_any_activation': ['independent delta/guard review and any required human security decision',
                'fresh complete Agent image inventory, live runtime proof and vulnerability scan under original rules',
                'complete current-revision raw CodeQL SARIF and all final-commit CI/Security'],
            'expires_at': manifest['expires_at']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-draft', action='store_true', help='Source-only preview; never activates security acceptance')
    parser.add_argument('--record-codeql-job', action='store_true', help='Pinned active workflow only: bind this successful current language analysis to unchanged raw SARIF')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--language', choices=sorted(LANGUAGE_CONTEXTS))
    parser.add_argument('--sarif-directory', type=Path, help='Optional raw corpus comparison; does not prove current-SHA provenance')
    args = parser.parse_args()
    try:
        if args.record_codeql_job:
            if args.review_draft or not args.directory or not args.language:
                raise RuntimeError('Recording requires an explicit current language/directory and cannot use draft review mode')
            result = write_codeql_receipt(ROOT, args.directory, args.language, activation.MODE)
        else:
            result = review_draft(review_only=args.review_draft, sarif_directory=args.sarif_directory)
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'effective_security_acceptance': False, 'reason': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
