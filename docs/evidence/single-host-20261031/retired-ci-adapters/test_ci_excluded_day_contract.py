"""Guard every non-causal field; no HTTP, Docker or application writes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from tools import ci_excluded_day_contract as contract


def pair():
    before = {'day_index': 1, 'schedule_stale': True,
              'feasibility': {'status': 'unverified', 'solver_status': 'SCHEDULE_DEPENDENCIES_CHANGED', 'unknown': ['schedule_unknown_or_stale']},
              'attractions': [{'poi_id': 'one', 'location': {'longitude': 121.0}}, {'poi_id': 'two'}],
              'meals': [{'activity_id': 'meal-one'}], 'timeline': [{'start_at': '10:00', 'end_at': '11:00', 'status': 'unverified'}],
              'locks': [{'activity_id': 'one'}], 'schedule_dependencies': {'input_hash': 'original', 'expires_at': 'original'},
              'facts': {'source': 'synthetic'}}
    proposed = copy.deepcopy(before)
    proposed['feasibility']['solver_status'] = 'PLANNING_INPUT_CHANGED'
    return before, proposed


class ExcludedDayContractTests(unittest.TestCase):
    def test_only_two_reviewed_reasons_normalize_and_inputs_are_not_modified(self):
        before, after = pair(); original = copy.deepcopy((before, after))
        result = contract.compare_excluded_day(before, after)
        self.assertTrue(result['passed']); self.assertTrue(result['stale_unverified_on_both'])
        self.assertEqual(['/feasibility/solver_status'], result['original_diff_paths'])
        self.assertEqual([], result['remaining_diff_paths'])
        self.assertEqual(original, (before, after))
        self.assertTrue(contract.compare_excluded_day(after, before)['passed'])

    def test_times_order_poi_meals_binding_locks_facts_other_feasibility_fields_all_reject(self):
        mutations = [lambda d: d['timeline'][0].update(start_at='10:01'),
                     lambda d: d['attractions'].reverse(), lambda d: d['attractions'][0].update(poi_id='other'),
                     lambda d: d['attractions'][0]['location'].update(longitude=122.0),
                     lambda d: d['meals'][0].update(activity_id='other'),
                     lambda d: d['schedule_dependencies'].update(input_hash='renewed'),
                     lambda d: d['locks'].clear(), lambda d: d['facts'].update(source='other'),
                     lambda d: d['feasibility']['unknown'].append('new-gap')]
        for mutate in mutations:
            before, after = pair(); mutate(after)
            result = contract.compare_excluded_day(before, after)
            self.assertFalse(result['passed']); self.assertTrue(result['remaining_diff_paths'])

    def test_fresh_missing_unknown_status_or_reason_never_bypass(self):
        mutations = [lambda d: d.update(schedule_stale=False), lambda d: d.update(schedule_stale=1),
                     lambda d: d.pop('schedule_stale'), lambda d: d['feasibility'].update(status='feasible'),
                     lambda d: d['feasibility'].update(solver_status='OTHER_REASON'),
                     lambda d: d['feasibility'].pop('solver_status')]
        for mutate in mutations:
            before, after = pair(); mutate(after)
            self.assertFalse(contract.compare_excluded_day(before, after)['passed'])

    def test_source_hash_drift_blocks_and_default_never_executes_scenario(self):
        original = contract.SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'scenario.py'; source.write_bytes(original + b'\n# unreviewed\n')
            with self.assertRaisesRegex(RuntimeError, 'source drift'): contract.prepare_source(source)
        reviewed = contract.prepare_source()
        self.assertIn(contract.REVIEWED_CHECK, reviewed)
        with patch.object(contract.runpy, 'run_path', side_effect=AssertionError('must not run')):
            self.assertEqual(0, contract.main([]))
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), self.assertRaises(RuntimeError):
                contract.main(['--run-ci'])

    def test_diagnostic_mode_keeps_original_equality_and_writes_actual_fixture_payload(self):
        source = contract.prepare_source(diagnostic_only=True)
        self.assertEqual(1, source.count(contract.ORIGINAL_CHECK))
        self.assertIn('original_equality_enforced', source)
        before, proposed = pair()
        self.assertTrue(contract.compare_excluded_day(before, proposed)['passed'])
        checks = []
        def check(name, condition):
            checks.append((name, condition))
            if not condition:
                raise AssertionError(name)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'final-scenarios.json'
            scope = {'compare_excluded_day': contract.compare_excluded_day,
                     'before': {'plan': {'days': [None, before]}},
                     'proposal': {'plan': {'days': [None, proposed]}},
                     'report': {}, 'output': output, 'json': json, 'check': check}
            with self.assertRaisesRegex(AssertionError, 'preserves excluded middle day exactly'):
                exec(compile(textwrap.dedent(contract.DIAGNOSTIC_CHECK), '<diagnostic-only-test>', 'exec'), scope)
            saved = json.loads((Path(folder) / 'final-scenarios-excluded-day-diagnostic.json').read_text(encoding='utf-8'))
            self.assertEqual('original_equality_enforced', saved['mode'])
            self.assertFalse(saved['original_whole_day_equal'])
            self.assertTrue(saved['comparison']['passed'])
            self.assertEqual(before, saved['before_day']); self.assertEqual(proposed, saved['proposed_day'])
            self.assertTrue(checks[0][1]); self.assertFalse(checks[1][1])


if __name__ == '__main__': unittest.main()
