"""Security policy counterexamples: incomplete scans never count as clean."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date

spec = importlib.util.spec_from_file_location('security_gate', Path(__file__).resolve().parents[1] / 'security_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateFailureTests(unittest.TestCase):
    def test_unavailable_nonzero_empty_and_skipped_results_cannot_pass(self):
        self.assertEqual('error', gate.classify(125, {}, 0))
        self.assertEqual('error', gate.classify(0, None, 0))
        self.assertEqual('error', gate.classify(1, {}, 0))
        self.assertEqual('error', gate.classify(0, {}, 0, skipped=1))
        self.assertEqual('blocked_findings', gate.classify(2, {}, 1))

    def test_codeql_high_result_blocks_while_medium_is_reported_without_block(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            sample = {'runs': [{'tool': {'driver': {'name': 'CodeQL', 'rules': [
                {'id': 'critical', 'properties': {'security-severity': '9.8'}},
                {'id': 'medium', 'properties': {'security-severity': '5.0'}}]}},
                'results': [{'ruleId': 'critical'}, {'ruleId': 'medium'}]}]}
            (folder / 'test.sarif').write_text(json.dumps(sample), encoding='utf-8')
            self.assertEqual(1, gate.sarif(folder)[0]['blocking_findings'])
            self.assertEqual('blocked_findings', gate.sarif(folder)[0]['status'])

    def test_missing_or_malformed_codeql_output_is_failure(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            with self.assertRaises(RuntimeError):
                gate.sarif(folder)
            (folder / 'bad.sarif').write_text('{"runs":[]}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                gate.sarif(folder)
            (folder / 'bad.sarif').write_text('{}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                gate.sarif(folder)

    def test_vex_is_exact_unexpired_source_bound_and_never_default(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'client.py'
            source.write_text('embedded_only = True\n', encoding='utf-8')
            assessment = {'ecosystem': 'PyPI', 'package': 'chromadb', 'version': '1.5.9', 'ids': ['CVE-fixture'],
                          'status': 'not_affected', 'justification': 'vulnerable_code_not_in_execute_path', 'references': ['https://example.invalid/advisory']}
            policy = {'expires_on': '2026-11-25', 'source_bindings': {'client.py': gate.source_digest(source)}, 'assessments': [assessment]}
            path = root / 'vex.json'
            path.write_text(json.dumps(policy), encoding='utf-8')
            verified = gate.load_vex(path, today=date(2026, 10, 26), root=root)
            self.assertTrue(gate.contextual_not_affected(verified, 'chromadb', '1.5.9', ['CVE-fixture']))
            self.assertFalse(gate.contextual_not_affected(None, 'chromadb', '1.5.9', ['CVE-fixture']))
            self.assertFalse(gate.contextual_not_affected(verified, 'chromadb', '1.5.10', ['CVE-fixture']))
            self.assertFalse(gate.contextual_not_affected(verified, 'chromadb', '1.5.9', ['CVE-new']))
            with self.assertRaises(RuntimeError):
                gate.load_vex(path, today=date(2026, 11, 26), root=root)
            source.write_text('embedded_only = False\n', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                gate.load_vex(path, today=date(2026, 10, 26), root=root)


if __name__ == '__main__':
    unittest.main()
