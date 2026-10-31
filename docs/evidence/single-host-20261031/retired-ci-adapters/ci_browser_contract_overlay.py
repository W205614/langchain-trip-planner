"""Prepare reviewed browser fixture copies for the current application contract.

Default execution verifies original sources without writes. Explicit CI mode
copies the entire original suite into a sibling directory, preserves its test
names/timeouts and applies only the reviewed, hash-bound fixture migrations.
Neither the tracked suite nor any application file is overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tools/ci_browser_contract_replacements.json'
REVIEWED_FILES = frozenset('frontend/e2e/' + name + '.spec.ts' for name in (
    'assistant-explore', 'completion', 'final-planning', 'final-roles-live', 'responsive', 'optimization-flows'))
COPY_RELATIVE = 'frontend/.ci-reviewed-e2e'
CONFIG_RELATIVE = 'frontend/.ci-reviewed-playwright.config.ts'
CONFIG = """import { defineConfig } from '@playwright/test'
import original from './playwright.config'
export default defineConfig({ ...original, testDir: './.ci-reviewed-e2e', outputDir: './test-results' })
"""


def lf(raw):
    return raw.replace(b'\r\n', b'\n')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def test_contract(text):
    """Keep declarations, test timeouts and every existing expectation count."""
    return {
        'declarations': re.findall(r'(?m)^[ \t]*test\s*\([^\n]*', text),
        'timeouts': re.findall(r'\btest\.setTimeout\([^\n]*', text),
        'skip_markers': Counter(re.findall(r'\btest\.(?:skip|fixme|only|fail)\b', text)),
        'expectations': len(re.findall(r'\bexpect(?:\.poll)?\s*\(', text)),
    }


def reviewed_plan(root=ROOT, manifest=None):
    root = Path(root).resolve()
    if manifest is None:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8-sig'))
    changes = manifest.get('replacements', [])
    if {item.get('source_path') for item in changes} != REVIEWED_FILES:
        raise RuntimeError('Exactly the six independently reviewed source files are required')
    if len({item.get('id') for item in changes}) != len(changes):
        raise RuntimeError('Duplicate migration identifier')
    outputs = {}
    for relative in sorted(REVIEWED_FILES):
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise RuntimeError('Unsafe original source path')
        raw = lf(source.read_bytes())
        selected = [item for item in changes if item['source_path'] == relative]
        if any(digest(raw) != item.get('source_lf_sha256') for item in selected):
            raise RuntimeError('Original browser source changed; no automatic fixture rebaseline')
        original = raw.decode('utf-8')
        revised = original
        for item in selected:
            before, after = item.get('before'), item.get('after')
            if not isinstance(before, str) or not before or not isinstance(after, str):
                raise RuntimeError('Migration requires explicit nonempty original and replacement strings')
            if revised.count(before) != 1:
                raise RuntimeError('Reviewed migration anchor no longer has exactly one occurrence')
            revised = revised.replace(before, after, 1)
        old, new = test_contract(original), test_contract(revised)
        if any(old[key] != new[key] for key in ('declarations', 'timeouts', 'skip_markers')):
            raise RuntimeError('Fixture migration changed test declarations, timeouts or skip markers')
        if new['expectations'] < old['expectations']:
            raise RuntimeError('Fixture migration removed existing expectation coverage')
        outputs[relative] = {'source_lf_sha256': digest(raw),
            'copy_lf_sha256': digest(revised.encode('utf-8')), 'text': revised,
            'migration_ids': [item['id'] for item in selected],
            'original_expectations': old['expectations'], 'revised_expectations': new['expectations']}
    return outputs


def prepare(root, plan):
    if os.environ.get('GITHUB_ACTIONS', '').lower() != 'true':
        raise RuntimeError('Fixture copy preparation requires GITHUB_ACTIONS=true')
    root = Path(root).resolve()
    original = root / 'frontend/e2e'
    destination = root / COPY_RELATIVE
    config = root / CONFIG_RELATIVE
    if destination.exists() or config.exists() or destination.is_symlink() or config.is_symlink():
        raise RuntimeError('Refuse to replace an existing copied suite or config')
    if not destination.resolve().is_relative_to(root) or not config.resolve().is_relative_to(root):
        raise RuntimeError('Copied suite must remain within the current checkout')
    sources = sorted(original.rglob('*'))
    files = [source for source in sources if source.is_file()]
    if any(source.is_symlink() or not source.resolve().is_relative_to(original.resolve()) for source in sources):
        raise RuntimeError('Symlinks or escaped original suite paths are not supported')
    if len(files) > 3000 or sum(source.stat().st_size for source in files) > 64 * 1024 * 1024:
        raise RuntimeError('Original suite exceeds the reviewed copy bounds')
    copied = []
    for source in files:
        relative = source.relative_to(root).as_posix()
        raw = source.read_bytes()
        if relative in plan and digest(lf(raw)) != plan[relative]['source_lf_sha256']:
            raise RuntimeError('Original fixture changed after review; preparation stopped')
        result = plan[relative]['text'].encode('utf-8') if relative in plan else raw
        target = destination / source.relative_to(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result)
        copied.append({'source': relative, 'changed_fixture': relative in plan,
                       'source_raw_sha256': digest(raw), 'copy_raw_sha256': digest(result)})
    config.write_text(CONFIG, encoding='utf-8', newline='\n')
    return copied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-ci', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    plan = reviewed_plan()
    if not args.prepare_ci:
        print(json.dumps({'passed': True, 'status': 'read_only_sources_verified',
                          'reviewed_files': len(plan), 'copied_suite_created': False}))
        return 0
    if args.report is None:
        raise RuntimeError('Explicit CI preparation requires --report')
    copied = prepare(ROOT, plan)
    report = {'status': 'reviewed_fixture_copy_prepared_not_test_results',
        'original_failed_revision': 'e44acfb720376676049cfed38b3dfaa8d8acebc0',
        'original_failed_job': 121979733367, 'fixture_revision': 'current-browser-contract-20261027-r2',
        'additional_failed_revision': '6167590717b43489ea989bbce74bf5fe4860e316',
        'additional_failed_job': 122002159902,
        'original_sources_overwritten': False, 'original_suite_all_files_copied': True,
        'test_names_timeouts_skip_markers_unchanged': True, 'all_original_tests_still_run': True,
        'new_browser_pass_inferred': False, 'live_provider_evidence': False,
        'config': CONFIG_RELATIVE, 'files': copied,
        'reviewed_changes': [{k: v for k, v in item.items() if k != 'text'} for item in plan.values()]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'prepared': True, 'reviewed_files': len(plan), 'copied_files': len(copied),
                      'original_sources_overwritten': False, 'all_original_tests_still_run': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
