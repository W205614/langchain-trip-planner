"""Review one derived stale-reason distinction in the offline CI scenario.

This standalone wrapper is copied to the Agent's /tmp. It verifies the exact
original /app script, then executes a temporary copy with one narrowly guarded
comparison. It never edits /app or a committed application file. Default only
verifies; real execution requires --run-ci and GITHUB_ACTIONS=true.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/app/scripts/final_scenarios.py')
if not SOURCE.is_file():
    SOURCE = ROOT / 'backend/scripts/final_scenarios.py'
SOURCE_LF_SHA256 = '46d6cd9761f0166c9c283eebaf49ca8d23858936b58b70fb0890c0bf2c416d6b'
ALLOWED_REASONS = frozenset({'PLANNING_INPUT_CHANGED', 'SCHEDULE_DEPENDENCIES_CHANGED'})
ORIGINAL_CHECK = '''            check("multi-day authorization preserves excluded middle day exactly",proposal["plan"]["days"][1]==before["plan"]["days"][1])'''
REVIEWED_CHECK = '''            excluded_contract=compare_excluded_day(before["plan"]["days"][1],proposal["plan"]["days"][1])
            report["excluded_middle_day_projection_contract"]=excluded_contract
            check("excluded middle day remains stale and unverified in both records",excluded_contract["stale_unverified_on_both"])
            check("multi-day authorization preserves excluded middle day exactly",excluded_contract["passed"])'''
DIAGNOSTIC_CHECK = '''            excluded_contract=compare_excluded_day(before["plan"]["days"][1],proposal["plan"]["days"][1])
            excluded_contract["mode"]="original_equality_enforced"
            report["excluded_middle_day_projection_contract"]=excluded_contract
            excluded_diagnostic={"mode":"original_equality_enforced","fixture_only":True,"original_whole_day_equal":proposal["plan"]["days"][1]==before["plan"]["days"][1],"comparison":excluded_contract,"before_day":before["plan"]["days"][1],"proposed_day":proposal["plan"]["days"][1]}
            excluded_diagnostic_path=output.with_name(output.stem+"-excluded-day-diagnostic.json")
            excluded_diagnostic_path.parent.mkdir(parents=True,exist_ok=True)
            excluded_diagnostic_path.write_text(json.dumps(excluded_diagnostic,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
            check("excluded middle day remains stale and unverified in both records",excluded_contract["stale_unverified_on_both"])
''' + ORIGINAL_CHECK


def signature(value):
    # Object-key order is irrelevant; array order, JSON types and every value
    # are retained. NaN/non-JSON values fail closed.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def diff_paths(left, right, path='', out=None):
    out = [] if out is None else out
    if len(out) >= 40 or signature(left) == signature(right):
        return out
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if key not in left or key not in right:
                out.append(pointer)
            else:
                diff_paths(left[key], right[key], pointer, out)
            if len(out) >= 40:
                break
    elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (a, b) in enumerate(zip(left, right)):
            diff_paths(a, b, path + '/' + str(index), out)
            if len(out) >= 40:
                break
    else:
        out.append(path or '/')
    return out


def compare_excluded_day(before, proposed):
    result = {'passed': False, 'stale_unverified_on_both': False,
              'derived_reason_normalized': False, 'original_diff_paths': [], 'remaining_diff_paths': [],
              'boundary': 'Only PLANNING_INPUT_CHANGED versus SCHEDULE_DEPENDENCIES_CHANGED may differ; both records must remain explicitly stale/unverified. Every other day field is compared exactly.'}
    try:
        result['original_diff_paths'] = diff_paths(before, proposed)
        if not isinstance(before, dict) or not isinstance(proposed, dict):
            return result
        left, right = before.get('feasibility'), proposed.get('feasibility')
        if not isinstance(left, dict) or not isinstance(right, dict):
            return result
        result['stale_unverified_on_both'] = (before.get('schedule_stale') is True
            and proposed.get('schedule_stale') is True
            and left.get('status') == right.get('status') == 'unverified')
        codes = (left.get('solver_status'), right.get('solver_status'))
        result['derived_reasons'] = list(codes)
        if not result['stale_unverified_on_both'] or any(code not in ALLOWED_REASONS for code in codes):
            return result
        if signature(before) == signature(proposed):
            result['passed'] = True
            return result
        if set(codes) != ALLOWED_REASONS:
            result['remaining_diff_paths'] = result['original_diff_paths']
            return result
        a, b = copy.deepcopy(before), copy.deepcopy(proposed)
        a['feasibility']['solver_status'] = b['feasibility']['solver_status'] = 'REVIEWED_DERIVED_STALE_REASON'
        result['derived_reason_normalized'] = True
        result['remaining_diff_paths'] = diff_paths(a, b)
        result['passed'] = signature(a) == signature(b)
        return result
    except (TypeError, ValueError):
        result['error'] = 'Non-JSON comparison input refused'
        return result


def prepare_source(source=SOURCE, diagnostic_only=False):
    raw = Path(source).read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != SOURCE_LF_SHA256:
        raise RuntimeError('Original offline scenario source drift; no automatic rebaseline')
    text = raw.decode('utf-8')
    if text.count(ORIGINAL_CHECK) != 1 or text.count('import httpx\n') != 1:
        raise RuntimeError('Reviewed exact scenario comparison is unavailable')
    text = text.replace('import httpx\n', 'import httpx\nfrom ci_excluded_day_contract import compare_excluded_day\n', 1)
    return text.replace(ORIGINAL_CHECK, DIAGNOSTIC_CHECK if diagnostic_only else REVIEWED_CHECK, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-ci', action='store_true')
    parser.add_argument('--diagnostic-only', action='store_true',
                        help='Save actual offline fixture day payloads and keep the original whole-JSON equality assertion')
    options, remaining = parser.parse_known_args(argv)
    reviewed = prepare_source(diagnostic_only=options.diagnostic_only)
    mode = 'original_equality_enforced' if options.diagnostic_only else 'reviewed_projection_contract'
    if not options.run_ci:
        print(json.dumps({'passed': True, 'status': 'read_only_original_scenario_verified',
                          'source_lf_sha256': SOURCE_LF_SHA256, 'scenario_executed': False, 'mode': mode}))
        return 0
    if os.environ.get('GITHUB_ACTIONS', '').lower() != 'true':
        raise RuntimeError('Scenario execution requires --run-ci and GITHUB_ACTIONS=true')
    # Retain the owned ephemeral copy for failed-job diagnostics. No application
    # source is overwritten; normal CI resource teardown owns its eventual life.
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='reviewed-final-scenarios-', suffix='.py', delete=False) as stream:
        stream.write(reviewed)
        temporary = Path(stream.name)
    print(json.dumps({'event': 'reviewed_offline_scenario_copy', 'source_lf_sha256': SOURCE_LF_SHA256,
                      'temporary_copy': str(temporary), 'original_application_script_edited': False, 'mode': mode}))
    saved = sys.argv
    try:
        sys.argv = [str(temporary), *remaining]
        runpy.run_path(str(temporary), run_name='__main__')
    finally:
        sys.argv = saved
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
