"""Residual risk requires the exact human scope, components and runtime receipt."""
import copy
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools import image_policy, risk_acceptance as risk
from tools import security_gate as gate

NOW = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]


class AcceptanceTests(unittest.TestCase):
    def approval(self, root):
        (root / 'authorization.json').write_bytes((REPO / '.github/security-risk-authorization.json').read_bytes())
        return {'schema_version': 1, 'expires_at': '2026-11-09T23:59:59+08:00',
                'effective_scope': 'controlled_single_host_local_demo_only',
                'deployment_window_granted': False, 'classification': 'still_vulnerable_temporarily_risk_accepted_not_fixed',
                'authorization': {'record_path': 'authorization.json', 'record_sha256': risk.PUBLIC_AUTHORIZATION_SHA256,
                                  'original_record_sha256': risk.ORIGINAL_AUTHORIZATION_SHA256},
                'approved_business_revision': risk.APPROVED_REVISION, 'allowed_cve_ids': sorted(risk.ALLOWED_IDS),
                'runtime_required': dict(risk.REQUIRED_RUNTIME),
                'approved_image': {'image_id': risk.APPROVED_IMAGE, 'distribution': 'debian', 'release': '13',
                                   'packages': {'libc6': '2.41-12+deb13u4'},
                                   'native_elf_sha256': {'/usr/lib/libc.so': 'a' * 64},
                                   'application_file_sha256': {'/app/app.py': 'b' * 64},
                                   'application_hash_algorithm': risk.APPLICATION_HASH_ALGORITHM}}

    def test_authorization_identity_exact_scope_ids_and_expiry_are_mandatory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); approval = self.approval(root); path = root / 'approval.json'
            path.write_text(json.dumps(approval)); self.assertEqual(approval, risk.load(path, root, NOW))
            for alter in [lambda a: a.update(expires_at='2026-11-10T00:00:00+08:00'),
                          lambda a: a.update(effective_scope='public_production'),
                          lambda a: a['allowed_cve_ids'].append('CVE-2099-UNKNOWN'),
                          lambda a: a['allowed_cve_ids'].pop(),
                          lambda a: a['authorization'].update(record_sha256='a' * 64),
                          lambda a: a['authorization'].update(original_record_sha256='a' * 64),
                          lambda a: a['approved_image'].update(image_id='sha256:' + 'b' * 64),
                          lambda a: a['approved_image'].update(application_hash_algorithm='raw-auto-rebaseline'),
                          lambda a: a['runtime_required'].update(readonly_rootfs=False)]:
                changed = copy.deepcopy(approval); alter(changed); path.write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): risk.load(path, root, NOW)
            path.write_text(json.dumps(approval))
            with self.assertRaises(RuntimeError): risk.load(path, root, datetime(2026, 11, 9, 16, tzinfo=timezone.utc))
            (root / 'authorization.json').write_text('{"accepted":true}')
            with self.assertRaises(RuntimeError): risk.load(path, root, NOW)

    def test_changed_package_native_elf_application_bytes_and_missing_full_inventory_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            approval = self.approval(Path(tmp))
            evidence = {**approval['approved_image'], 'native_inventory_complete': True}
            risk.validate_components(approval, evidence)
            for key, value in [('packages', {'libc6': 'changed'}), ('native_elf_sha256', {}),
                               ('application_file_sha256', {'/app/app.py': 'c' * 64}),
                               ('native_inventory_complete', False), ('application_hash_algorithm', 'unknown')]:
                changed = copy.deepcopy(evidence); changed[key] = value
                with self.assertRaises(RuntimeError): risk.validate_components(approval, changed)
            changed = copy.deepcopy(evidence); changed['native_elf_sha256']['/new/wheel.so'] = 'd' * 64
            with self.assertRaises(RuntimeError): risk.validate_components(approval, changed)

    def test_business_tree_and_worktree_recipe_changes_block_policy_only_head_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root).decode().strip()
            git('init', '-q'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'user.name', 'Fixture')
            for directory in risk.BUSINESS_DIRECTORIES:
                (root / directory).mkdir(); (root / directory / 'file.txt').write_text('approved\n')
            for recipe in ['docker-compose.yml', 'docker-compose.production.yml', 'docker-compose.validation.yml']:
                (root / recipe).write_text('services: {}\n')
            git('add', '.'); git('commit', '-qm', 'approved fixture'); anchor = git('rev-parse', 'HEAD')
            with patch.object(risk, 'APPROVED_REVISION', anchor):
                approval = {'approved_business_revision': anchor, **risk.collect_source_bindings(root, anchor)}
                (root / 'policy.txt').write_text('policy-only change'); git('add', '.'); git('commit', '-qm', 'policy fixture')
                self.assertTrue(risk.validate_sources(approval, root, anchor)['business_trees_equal'])
                (root / 'backend/file.txt').write_text('uncommitted bad change')
                with self.assertRaises(RuntimeError): risk.validate_sources(approval, root, anchor)
                git('add', '.'); git('commit', '-qm', 'business changed fixture')
                with self.assertRaises(RuntimeError): risk.validate_sources(approval, root, anchor)
                git('checkout', '-q', '--detach', anchor)
                (root / 'docker-compose.validation.yml').write_text('changed constraints\n')
                with self.assertRaises(RuntimeError): risk.validate_sources(approval, root, anchor)

    def test_stale_wrong_image_constraints_or_probe_runtime_receipt_cannot_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); approval = self.approval(root); (root / 'tools').mkdir()
            (root / 'tools/runtime_constraint_probe.py').write_bytes(b'reviewed probe\n')
            proof = {'schema_version': 1, 'passed': True, 'checked_at': NOW.isoformat(),
                     'constraints': dict(risk.REQUIRED_RUNTIME), 'image_id': risk.APPROVED_IMAGE,
                     'container_id': 'e' * 64, 'probe_source_sha256': risk.digest(b'reviewed probe\n')}
            for modify in [lambda p: p.update(checked_at=(NOW - timedelta(hours=25)).isoformat()),
                           lambda p: p.update(image_id='sha256:' + 'b' * 64),
                           lambda p: p.update(passed=False),
                           lambda p: p['constraints'].update(cap_bnd='00000000a80425fb'),
                           lambda p: p.update(probe_source_sha256='a' * 64)]:
                changed = copy.deepcopy(proof); modify(changed)
                with self.assertRaises(RuntimeError): risk.validate_runtime(approval, changed, risk.APPROVED_IMAGE, root, NOW)
            with self.assertRaises(RuntimeError):
                risk.verify(approval, {}, proof, root, risk.APPROVED_REVISION,
                            datetime(2026, 11, 9, 16, tzinfo=timezone.utc))

    def test_full_export_finds_system_wheel_elf_alias_and_app_and_rejects_partial_or_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'rootfs.tar'
            with tarfile.open(archive, 'w') as tar:
                for name, raw in [('usr/lib/libc.so.6', b'\x7fELFsystem'),
                                  ('usr/local/lib/python3.11/site-packages/wheel/native.so', b'\x7fELFwheel'),
                                  ('app/app.py', b'print(1)\r\n'), ('app/raw.bin', b'x\r\n'),
                                  ('app/__pycache__/app.pyc', b'ignored')]:
                    member = tarfile.TarInfo(name); member.size = len(raw); tar.addfile(member, io.BytesIO(raw))
                alias = tarfile.TarInfo('usr/lib/libc.so'); alias.type = tarfile.SYMTYPE; alias.linkname = 'libc.so.6'; tar.addfile(alias)
            got = image_policy.exported_file_inventory(archive)
            self.assertEqual(3, len(got['native_elf_sha256']))
            self.assertEqual(risk.digest(b'print(1)\n'), got['application_file_sha256']['/app/app.py'])
            self.assertEqual(risk.digest(b'x\r\n'), got['application_file_sha256']['/app/raw.bin'])
            self.assertNotIn('/app/__pycache__/app.pyc', got['application_file_sha256'])
            with tarfile.open(archive, 'a') as tar:
                duplicate = tarfile.TarInfo('app/app.py'); duplicate.size = 1; tar.addfile(duplicate, io.BytesIO(b'x'))
            with self.assertRaises(RuntimeError): image_policy.exported_file_inventory(archive)

    def test_text_normalization_is_narrow_not_bom_stripping_or_invalid_utf8_coercion(self):
        self.assertEqual(risk.application_digest('a.py', b'x\r\n'), risk.application_digest('a.py', b'x\n'))
        self.assertNotEqual(risk.application_digest('a.bin', b'x\r\n'), risk.application_digest('a.bin', b'x\n'))
        self.assertNotEqual(risk.application_digest('a.py', b'\xef\xbb\xbfx\n'), risk.application_digest('a.py', b'x\n'))
        self.assertEqual(risk.digest(b'\xff\r\n'), risk.application_digest('a.py', b'\xff\r\n'))

    def test_missing_old_wrong_image_and_expired_database_scan_receipts_block(self):
        receipt = {'matches': [], 'source': {'type': 'image', 'target': {'userInput': risk.APPROVED_IMAGE}},
                   'descriptor': {'timestamp': NOW.isoformat(), 'db': {'status': {'valid': True, 'built': (NOW - timedelta(hours=12)).isoformat()}}}}
        self.assertTrue(risk.validate_scan_receipt(receipt, risk.APPROVED_IMAGE, NOW, NOW)['fresh_scan_verified'])
        for alter in [lambda r: r['source']['target'].update(userInput='wrong image'),
                      lambda r: r['descriptor'].update(timestamp=(NOW - timedelta(hours=2)).isoformat()),
                      lambda r: r['descriptor']['db']['status'].update(valid=False),
                      lambda r: r['descriptor']['db']['status'].update(built=(NOW - timedelta(days=4)).isoformat())]:
            changed = copy.deepcopy(receipt); alter(changed)
            with self.assertRaises(RuntimeError): risk.validate_scan_receipt(changed, risk.APPROVED_IMAGE, NOW, NOW)
        with self.assertRaises(RuntimeError): risk.validate_scan_receipt({}, risk.APPROVED_IMAGE, NOW, NOW)

    def test_new_high_and_scanner_failure_cannot_be_rescued_by_valid_risk_acceptance(self):
        known = {'artifact': {'name': 'libc6', 'version': '2.41-12+deb13u4', 'type': 'deb'},
                 'vulnerability': {'id': 'CVE-2026-5435', 'severity': 'High'}}
        unknown = copy.deepcopy(known); unknown['vulnerability']['id'] = 'CVE-2099-UNKNOWN'
        entry = {'distribution': 'debian', 'release': '13', 'artifact_type': 'deb',
                 'artifact_versions': {'libc6': '2.41-12+deb13u4'},
                 'requires_dpkg': {'libc6': '2.41-12+deb13u4'}, 'ids': ['CVE-2026-5435'],
                 'classification': 'still_vulnerable_vendor_minor'}
        evidence = {'distribution': 'debian', 'release': '13', 'packages': {'libc6': '2.41-12+deb13u4'}}
        acceptance = {'status': 'effective_temporary_acceptance_still_vulnerable', 'allowed_cve_ids': sorted(risk.ALLOWED_IDS)}
        def inspect(command, **kwargs):
            expression = command[command.index('--format') + 1]
            if expression == '{{json .Id}}': value = json.dumps(risk.APPROVED_IMAGE)
            elif expression == '{{json .Config.Cmd}}':
                value = json.dumps(['uvicorn', 'app.agent_api.main:app', '--host', '0.0.0.0', '--port', '9000', '--workers', '1'])
            else: value = risk.APPROVED_REVISION
            return SimpleNamespace(returncode=0, stdout=value)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            for matches, code, expected in [([known, unknown], 2, 'blocked_findings'),
                                             ([known], 1, 'raises'), ([known], 2, 'pass_temporary_risk_acceptance')]:
                def scan(*args, **kwargs):
                    (destination / 'image-vulnerabilities.json').write_text(json.dumps({'source': {'type': 'image'}, 'matches': matches}))
                    return code
                with patch.object(gate, 'tool', return_value='unused'), patch.object(gate, 'run', side_effect=scan), \
                     patch.object(gate.subprocess, 'run', side_effect=inspect), \
                     patch.object(gate.subprocess, 'check_output', return_value=risk.APPROVED_REVISION), \
                     patch.object(image_policy, 'inventory', return_value=evidence), \
                     patch.object(risk, 'validate_sources', return_value={}), patch.object(risk, 'verify', return_value=acceptance), \
                     patch.object(risk, 'validate_scan_receipt', return_value={}):
                    if expected == 'raises':
                        with self.assertRaises(RuntimeError):
                            gate.image(destination, risk.APPROVED_IMAGE,
                                       vendor_policy={'expires_on': '2026-11-09', 'assessments': [entry]}, approval={'fixture': True}, runtime_proof={})
                        continue
                    result = gate.image(destination, risk.APPROVED_IMAGE,
                                        vendor_policy={'expires_on': '2026-11-09', 'assessments': [entry]}, approval={'fixture': True}, runtime_proof={})[0]
                self.assertEqual(expected, result['status'])
                self.assertEqual(len(matches), result['raw_high_critical_findings'])
                self.assertEqual(1, result['unfixed_risk_accepted_findings'])
                self.assertEqual('still_vulnerable_vendor_minor', next(iter(json.loads((destination / 'distribution-assessments.json').read_text())['assessments']))['classification'])


if __name__ == '__main__':
    unittest.main()
