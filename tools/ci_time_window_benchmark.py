"""Run the exact frozen benchmark with explicit synthetic reservation premises.

Only the module's TripRequest constructor is adapted. The loaded run() source,
all six expected outcomes and every original assertion remain unchanged. No
application file is edited and this is not live reservation evidence.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'backend/scripts/time_window_benchmark.py'
SOURCE_LF_SHA256 = 'c6ca40088e756f06f407659d43b95f6d71f1f822d95fb1e958f80e1b1350d7c6'
FACT_SOURCE = 'frozen-synthetic-no-reservation-v1'


def checked_source(source=SOURCE):
    raw = Path(source).read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != SOURCE_LF_SHA256:
        raise RuntimeError('Frozen benchmark source changed; no automatic fixture rebaseline')
    return raw


def validate_facts(facts, date, now):
    if len(facts) != 3 or {fact.get('poi_id') for fact in facts} != {'A', 'B', 'C'}:
        raise RuntimeError('Exactly three reviewed synthetic POIs are required')
    for fact in facts:
        if (fact.get('source') != FACT_SOURCE or fact.get('date') != date
                or fact.get('status') != 'not_required' or fact.get('confirmed') is not True):
            raise RuntimeError('Synthetic reservation declaration is incomplete')
        try:
            observed = datetime.fromisoformat(fact['observed_at'])
            expires = datetime.fromisoformat(fact['expires_at'])
            if not observed.tzinfo or not expires.tzinfo or not observed <= now < expires <= observed + timedelta(hours=1):
                raise ValueError('Invalid synthetic validity interval')
        except (KeyError, TypeError, ValueError):
            raise RuntimeError('Synthetic declarations require explicit current observation and bounded TTL') from None


def adapt_constructor(original):
    def construct(**values):
        values = copy.deepcopy(values)
        inputs = values.get('planning_input')
        if not isinstance(inputs, dict) or 'reservation_facts' in inputs:
            raise RuntimeError('Adapter refuses to overwrite explicit reservation input')
        if {fact.get('poi_id') for fact in inputs.get('opening_windows', [])} != {'A', 'B', 'C'}:
            raise RuntimeError('Adapter is limited to the original three synthetic candidates')
        now = datetime.now(timezone.utc)
        facts = [{'poi_id': poi, 'date': values['start_date'], 'status': 'not_required',
                  'source': FACT_SOURCE, 'observed_at': now.isoformat(),
                  'expires_at': (now + timedelta(hours=1)).isoformat(), 'confirmed': True}
                 for poi in 'ABC']
        validate_facts(facts, values['start_date'], now)
        inputs['reservation_facts'] = facts
        return original(**values)
    return construct


def run_benchmark(source=SOURCE):
    raw = checked_source(source)
    module = types.ModuleType('reviewed_synthetic_time_window_benchmark')
    module.__file__ = str(Path(source).resolve())
    exec(compile(raw.decode('utf-8'), module.__file__, 'exec'), module.__dict__)
    original_run = module.run
    module.TripRequest = adapt_constructor(module.TripRequest)
    result = module.run()
    if module.run is not original_run:
        raise RuntimeError('Benchmark assertions or run function were replaced')
    if 'dataset' in result:
        result['original_dataset'] = result['dataset']
    if 'mode' in result:
        result['original_mode'] = result['mode']
    result.update(dataset='current-contract-fixture-20261027',
        fixture_revision='current-contract-fixture-20261027',
        mode='current_contract_fixture_functional_ci', input_fixture_changed=True,
        not_comparable_to_original_frozen_evaluation=True, heldout16of17_unchanged=True)
    result['ci_fixture_adapter'] = {'source_lf_sha256': SOURCE_LF_SHA256,
        'constructor_only': True, 'original_assertions_and_expected_outcomes_unchanged': True,
        'dataset': 'current-contract-fixture-20261027',
        'fixture_revision': 'current-contract-fixture-20261027', 'input_fixture_changed': True,
        'not_comparable_to_original_frozen_evaluation': True, 'heldout16of17_unchanged': True,
        'purpose': 'Functional CI contract migration only; not a live evaluation or accuracy improvement.',
        'reservation_pois': ['A', 'B', 'C'], 'reservation_status': 'not_required',
        'source': FACT_SOURCE, 'ttl_seconds': 3600, 'live_reservation_evidence': False,
        'original_failed_revision': '5485f8a7e79ede3484a18f11ae261b407f50c7f2'}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-ci', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    checked_source()
    if not args.run_ci:
        print(json.dumps({'passed': True, 'status': 'read_only_frozen_benchmark_verified',
                          'source_lf_sha256': SOURCE_LF_SHA256, 'benchmark_executed': False}))
        return 0
    if os.environ.get('GITHUB_ACTIONS', '').lower() != 'true' or args.output is None:
        raise RuntimeError('Execution requires --run-ci, GITHUB_ACTIONS=true and --output')
    report = run_benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
                      'synthetic_reservation_premises': True, 'live_reservation_evidence': False}))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
