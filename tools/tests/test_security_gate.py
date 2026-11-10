"""Security policy counterexamples: incomplete scans never count as clean."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import date

spec = importlib.util.spec_from_file_location('security_gate', Path(__file__).resolve().parents[1] / 'security_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateFailureTests(unittest.TestCase):
    def test_new_not_affected_classes_are_counted_separately_while_unknown_raw_finding_blocks(self):
        base = {'distribution': 'debian', 'release': '13', 'artifact_type': 'deb',
                'artifact_versions': {'libc6': 'reviewed'}, 'requires_dpkg': {'libc6': 'reviewed'},
                'review_record': 'review.md'}
        execution = dict(base, classification='not_affected_execution_path', ids=['reviewed-execution'],
                         requires_source_lf_sha256={'/app/app/main.py': 'a'*64})
        configuration = dict(base, classification='not_affected_build_configuration', ids=['reviewed-configuration'],
                             file_sha256={'/lib/libc.so.6': 'b'*64}, requires_architecture='amd64')
        policy = {'schema_version': 2, 'review_policy': 'source_and_artifact_bound',
                  'source_bindings': {'review.md': 'c'*64}, 'assessments': [execution, configuration]}
        evidence = {'distribution': 'debian', 'release': '13', 'packages': {'libc6': 'reviewed'},
                    'source_lf_sha256': {'/app/app/main.py': 'a'*64},
                    'file_sha256': {'/lib/libc.so.6': 'b'*64}, 'architecture': 'amd64'}
        sample = {'source': {'type': 'image'}, 'matches': [
            {'artifact': {'type': 'deb', 'name': 'libc6', 'version': 'reviewed'},
             'vulnerability': {'id': identifier, 'severity': 'Unknown'}}
            for identifier in ('reviewed-execution', 'reviewed-configuration', 'raw-unknown')]}
        def inspection(command, **kwargs):
            field = command[-2]
            if field == '{{json .Id}}': output = '"sha256:fixture"'
            elif field == '{{json .Config.Cmd}}': output = json.dumps(['uvicorn', 'app.agent_api.main:app', '--host', '0.0.0.0', '--port', '9000', '--workers', '1'])
            else: output = 'reviewed-head'
            return type('Inspection', (), {'returncode': 0, 'stdout': output})()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(gate, 'tool', return_value='grype'), \
             patch.object(gate.subprocess, 'run', side_effect=inspection), \
             patch.object(gate.subprocess, 'check_output', return_value='reviewed-head'), \
             patch.object(gate, 'run', return_value=2), \
             patch.object(gate, 'report', return_value=sample), \
             patch.object(gate.image_policy, 'inventory', return_value=evidence):
            result = gate.image(Path(folder), 'candidate', vendor_policy=policy, all_severities=True)[0]
            self.assertEqual('blocked_findings', result['status'])
            self.assertEqual(1, result['blocking_findings'])
            self.assertEqual(1, result['distribution_assessments']['not_affected_execution_path'])
            self.assertEqual(1, result['distribution_assessments']['not_affected_build_configuration'])
            self.assertEqual(0, result['distribution_assessments']['not_affected_component_absent'])
            sample['matches'].pop()
            result = gate.image(Path(folder), 'candidate', vendor_policy=policy, all_severities=True)[0]
            self.assertEqual('pass_vendor_policy', result['status'])
            self.assertEqual(0, result['blocking_findings'])

    def test_all_severity_image_gate_blocks_low_and_unknown_even_if_scanner_exit_is_zero(self):
        sample = {'matches': [{'vulnerability': {'id': 'low-fixture', 'severity': 'Low'}},
                              {'vulnerability': {'id': 'unknown-fixture', 'severity': 'Unknown'}}],
                  'source': {'type': 'image'}}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(gate, 'tool', return_value='grype'), \
                 patch.object(gate.subprocess, 'run', return_value=type('Inspection', (), {'returncode': 0, 'stdout': '"sha256:fixture"'})()), \
                 patch.object(gate, 'run', return_value=0), \
                 patch.object(gate, 'report', return_value=sample):
                records = gate.image(Path(folder), 'candidate', all_severities=True)
                self.assertEqual('blocked_findings', records[0]['status'])
                self.assertEqual(2, records[0]['blocking_findings'])
                self.assertEqual(0, records[0]['raw_high_critical_findings'])
                self.assertEqual('all', records[0]['severity_scope'])
                self.assertEqual(2, records[0]['all_findings'])

    def test_all_severity_image_gate_cannot_apply_residual_risk_acceptance(self):
        with self.assertRaisesRegex(RuntimeError, 'does not accept residual'):
            gate.image(Path('.'), 'candidate', approval={'approved': True}, all_severities=True)

    def test_unavailable_nonzero_empty_and_skipped_results_cannot_pass(self):
        self.assertEqual('error', gate.classify(125, {}, 0))
        self.assertEqual('error', gate.classify(0, None, 0))
        self.assertEqual('error', gate.classify(1, {}, 0))
        self.assertEqual('error', gate.classify(0, {}, 0, skipped=1))
        self.assertEqual('blocked_findings', gate.classify(2, {}, 1, valid_codes=(0, 2)))
        self.assertEqual('error', gate.classify(1, {'partial': True}, 3, valid_codes=(0, 2)))
        self.assertEqual('error', gate.classify(2, {'partial': True}, 3, valid_codes=(0, 1)))
        self.assertFalse(gate.image_document({}))
        self.assertFalse(gate.image_document({'matches': []}))
        self.assertFalse(gate.image_document({'matches': [], 'source': {'type': 'directory'}}))
        self.assertTrue(gate.image_document({'matches': [], 'source': {'type': 'image'}}))

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
            policy = {'schema_version': 1, 'expires_on': '2026-11-25', 'source_bindings': {'client.py': gate.source_digest(source)}, 'assessments': [assessment]}
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

    def test_source_bound_vex_survives_calendar_but_new_cve_version_or_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = root/'client.py'
            source.write_text('embedded_only = True\n', encoding='utf-8')
            assessment = {'ecosystem':'PyPI', 'package':'chromadb', 'version':'1.5.9', 'ids':['CVE-fixture'],
                          'status':'not_affected', 'justification':'vulnerable_code_not_in_execute_path',
                          'references':['https://example.invalid/advisory']}
            policy = {'schema_version':2, 'review_policy':'source_and_execution_path_bound',
                      'source_bindings':{'client.py':gate.source_digest(source)}, 'assessments':[assessment]}
            path = root/'vex.json'; path.write_text(json.dumps(policy), encoding='utf-8')
            verified = gate.load_vex(path, today=date(2040,1,1), root=root)
            self.assertTrue(gate.contextual_not_affected(verified,'chromadb','1.5.9',['CVE-fixture']))
            self.assertFalse(gate.contextual_not_affected(verified,'chromadb','1.5.9',['CVE-new']))
            self.assertFalse(gate.contextual_not_affected(verified,'chromadb','new-version',['CVE-fixture']))
            self.assertFalse(gate.contextual_not_affected(None,'chromadb','1.5.9',['CVE-fixture']))
            source.write_text('remote_server = True\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError,'source binding changed'):
                gate.load_vex(path, today=date(2040,1,1), root=root)


if __name__ == '__main__':
    unittest.main()
