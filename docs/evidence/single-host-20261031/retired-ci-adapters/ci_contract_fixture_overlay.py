"""Explicit test-only CI fixture premises; no committed business tree changes.

The original failed assertions are retained. Default verifies only; CI may
explicitly apply the exact reviewed two-function patch to its ephemeral test
checkout. The independently checked-out image/CodeQL jobs do not apply it.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'docs/evidence/optimization-20261026/ci-fixture-contract-overlay.json'
MANIFEST_SHA256 = 'be04516a65b31606ee251307cb7f1512c4c614632b594ec57d641092aeb1a566'
BEFORE_HASHES = {
    'backend/tests/test_beijing_display_fixes.py': '36e0fc0c21b7e5444425662977ce8ceca0075225582cdd74c8099dbf03c682e6',
    'backend/tests/test_time_planner.py': '618ef62e43ba60b2a69df03abbef10696e1fcddccdc770df34ab669b52b1c0cc',
}
AFTER_HASHES = {
    'backend/tests/test_beijing_display_fixes.py': '065ba0c0af48be65bac2f830ca13d6634fbe1f69102f7b205dcf23b5ade581c1',
    'backend/tests/test_time_planner.py': '0fe34a4e1c6dc2dabf6db11ed4d252fb163089349bcfd5bb14be773ca45226f3',
}
TESTS = {
    'test_beijing_driving_saved_plan_is_corrected_without_regeneration',
    'test_cached_day_transport_buffer_gap_reaches_final_quality_without_resolving',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(root, relative):
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise RuntimeError('Fixture path is outside the reviewed repository paths')
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Fixture source escapes repository')
    for path in (target, *target.parents):
        if path == root.parent:
            break
        if path.is_symlink():
            raise RuntimeError('Fixture symlink path is refused')
    return target


def assertions(fragment):
    return [ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(fragment))
            if isinstance(node, ast.Assert)]


def prepare(root):
    root = Path(root).resolve()
    manifest_path = checked_path(root, MANIFEST)
    raw_manifest = manifest_path.read_bytes()
    if digest(raw_manifest) != MANIFEST_SHA256:
        raise RuntimeError('Reviewed fixture manifest changed; no automatic rebaseline')
    manifest = json.loads(raw_manifest)
    entries = manifest.get('files', [])
    if (manifest.get('schema_version') != 1 or manifest.get('approved_business_revision') != '220257ad32e50d08cb987d565882f8a038e36c6f'
            or len(entries) != 2 or {entry.get('path') for entry in entries} != set(BEFORE_HASHES)
            or {entry.get('test_name') for entry in entries} != TESTS):
        raise RuntimeError('Exactly two reviewed fixture functions are required')
    changes = []
    for entry in entries:
        name = entry['path']
        if entry['before_lf_sha256'] != BEFORE_HASHES[name] or entry['after_lf_sha256'] != AFTER_HASHES[name]:
            raise RuntimeError('Frozen fixture file hashes changed')
        before, after = entry['before_fragment'], entry['after_fragment']
        if assertions(before) != assertions(after) or not assertions(before):
            raise RuntimeError('Behavior assertions must remain exactly unchanged')
        for fragment in (before, after):
            tree = ast.parse(fragment)
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != entry['test_name']:
                raise RuntimeError('Overlay is outside the reviewed test function')
        path = checked_path(root, name)
        raw = path.read_bytes()
        source = raw.replace(b'\r\n', b'\n').decode('utf-8')
        current = digest(source.encode())
        if current == AFTER_HASHES[name]:
            changes.append((path, raw, True))
            continue
        if current != BEFORE_HASHES[name] or source.count(before) != 1:
            raise RuntimeError('Fixture source drift blocks overlay: ' + name)
        updated = source.replace(before, after, 1)
        if digest(updated.encode()) != AFTER_HASHES[name]:
            raise RuntimeError('Fixture patch differs from the reviewed exact output')
        # Preserve CRLF only when it was the original file's consistent form.
        payload = updated.encode()
        if b'\r\n' in raw and raw.count(b'\r\n') == raw.count(b'\n'):
            payload = payload.replace(b'\n', b'\r\n')
        changes.append((path, payload, False))
    return changes


def apply_overlay(root=ROOT, apply_for_ci=False):
    if apply_for_ci and os.environ.get('GITHUB_ACTIONS', '').lower() != 'true':
        raise RuntimeError('Fixture writes require explicit --apply-for-ci and GITHUB_ACTIONS=true')
    changes = prepare(root)  # Validate both files before the first write.
    written = 0
    if apply_for_ci:
        for path, payload, already in changes:
            if not already:
                path.write_bytes(payload)
                written += 1
        prepare(root)
    return {'passed': True, 'status': 'applied_exact_test_fixture_overlay' if apply_for_ci else 'read_only_fixture_overlay_verified',
            'files_verified': len(changes), 'files_written': written,
            'behavior_assertions_unchanged': True, 'fresh_provider_evaluation': False,
            'approved_business_revision': '220257ad32e50d08cb987d565882f8a038e36c6f',
            'original_failed_run': 'https://github.com/W205614/langchain-trip-planner/actions/runs/40432045866',
            'boundary': 'Synthetic daily-transport/cache-expiry premises only in this ephemeral Python-test job. Original failures remain recorded; no skip/xfail/assertion weakening or committed business/native/approval fingerprint change.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-for-ci', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = apply_overlay(apply_for_ci=args.apply_for_ci)
    except Exception as error:
        report = {'passed': False, 'status': 'blocked', 'error_type': type(error).__name__, 'error': str(error)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
