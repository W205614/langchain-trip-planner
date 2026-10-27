import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import ci_time_window_benchmark as benchmark


class BenchmarkFixtureTests(unittest.TestCase):
    def test_only_adds_three_explicit_bounded_synthetic_reservations(self):
        original = {'start_date': '2026-11-01', 'planning_input': {
            'opening_windows': [{'poi_id': poi} for poi in 'ABC'], 'hotel_stays': [{'day_index': 0}]}}
        saved = copy.deepcopy(original)
        result = benchmark.adapt_constructor(lambda **values: values)(**original)
        self.assertEqual(saved, original)
        self.assertEqual(saved['planning_input']['opening_windows'], result['planning_input']['opening_windows'])
        self.assertEqual(saved['planning_input']['hotel_stays'], result['planning_input']['hotel_stays'])
        facts = result['planning_input']['reservation_facts']
        self.assertEqual(3, len(facts))
        self.assertTrue(all(fact['status'] == 'not_required' and fact['source'] == benchmark.FACT_SOURCE for fact in facts))
        benchmark.validate_facts(facts, original['start_date'], datetime.now(timezone.utc))

    def test_missing_source_or_ttl_and_explicit_reservations_fail_closed(self):
        now = datetime.now(timezone.utc)
        facts = [{'poi_id': poi, 'date': '2026-11-01', 'status': 'not_required',
                  'source': benchmark.FACT_SOURCE, 'confirmed': True, 'observed_at': now.isoformat(),
                  'expires_at': (now + timedelta(hours=1)).isoformat()} for poi in 'ABC']
        for key in ('source', 'observed_at', 'expires_at'):
            altered = copy.deepcopy(facts); altered[0].pop(key)
            with self.assertRaises(RuntimeError): benchmark.validate_facts(altered, '2026-11-01', now)
        with self.assertRaisesRegex(RuntimeError, 'overwrite'):
            benchmark.adapt_constructor(lambda **values: values)(start_date='2026-11-01', planning_input={'reservation_facts': []})

    def test_source_drift_and_non_ci_execution_refused_without_touching_assertions(self):
        raw = benchmark.checked_source()
        self.assertIn(b'assert actual==expected', raw)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'benchmark.py'; path.write_bytes(raw + b'\n# unreviewed\n')
            with self.assertRaisesRegex(RuntimeError, 'source changed'): benchmark.checked_source(path)
        with patch.object(benchmark, 'run_benchmark', side_effect=AssertionError('Must not execute')):
            self.assertEqual(0, benchmark.main([]))
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), self.assertRaises(RuntimeError):
                benchmark.main(['--run-ci', '--output', 'unused.json'])


if __name__ == '__main__': unittest.main()
