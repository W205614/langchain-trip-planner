"""Distribution calibration must not hide unrelated or still-vulnerable findings."""
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import image_policy


class DistributionPolicyTests(unittest.TestCase):
    def sample(self):
        entry = {'distribution': 'debian', 'release': '13', 'artifact_type': 'deb',
                 'artifact_versions': {'libc6': '2.41-12+deb13u4'},
                 'requires_dpkg': {'libc6': '2.41-12+deb13u4'},
                 'ids': ['CVE-2026-5435'], 'classification': 'still_vulnerable_vendor_minor',
                 'vendor_status': 'vulnerable', 'vendor_severity': 'minor_no_dsa', 'references': ['https://security-tracker.debian.org/tracker/CVE-2026-5435'],
                 'reason': 'Exact Debian no-dsa Minor assessment, retaining vulnerable status'}
        match = {'artifact': {'name': 'libc6', 'version': '2.41-12+deb13u4', 'type': 'deb'},
                 'vulnerability': {'id': 'CVE-2026-5435', 'severity': 'High'}}
        evidence = {'distribution': 'debian', 'release': '13', 'packages': {'libc6': '2.41-12+deb13u4'}}
        return {'assessments': [entry]}, match, evidence

    def test_minor_remains_vulnerable_and_unknown_versions_ids_or_release_are_not_allowed(self):
        policy, match, evidence = self.sample()
        self.assertIsNone(image_policy.assess(policy, match, evidence))
        acceptance = {'status': 'effective_temporary_acceptance_still_vulnerable', 'allowed_cve_ids': ['CVE-2026-5435']}
        self.assertEqual('still_vulnerable_vendor_minor', image_policy.assess(policy, match, evidence, acceptance))
        self.assertEqual('vulnerable', policy['assessments'][0]['vendor_status'])
        for key, value in [('id', 'CVE-2099-9999')]:
            modified = json.loads(json.dumps(match)); modified['vulnerability'][key] = value
            self.assertIsNone(image_policy.assess(policy, modified, evidence, acceptance))
        modified = json.loads(json.dumps(match)); modified['artifact']['version'] = '2.41-12+deb13u5'
        self.assertIsNone(image_policy.assess(policy, modified, evidence, acceptance))
        evidence['release'] = '12'
        self.assertIsNone(image_policy.assess(policy, match, evidence, acceptance))
        self.assertIsNone(image_policy.assess(None, match, evidence))

    def test_backport_requires_actual_installed_revision_and_absence_is_not_assumed(self):
        policy, match, evidence = self.sample()
        policy['assessments'][0]['classification'] = 'fixed_by_distribution_backport'
        evidence['packages']['libc6'] = '2.41-12+deb13u3'
        self.assertIsNone(image_policy.assess(policy, match, evidence))
        evidence['packages']['libc6'] = '2.41-12+deb13u4'
        policy['assessments'][0]['classification'] = 'not_affected_component_absent'
        policy['assessments'][0]['absent_paths'] = ['/usr/bin/infocmp']
        self.assertIsNone(image_policy.assess(policy, match, evidence))
        evidence['absent_paths'] = ['/usr/bin/infocmp']
        self.assertEqual('not_affected_component_absent', image_policy.assess(policy, match, evidence))
        policy['assessments'][0]['file_sha256'] = {'/usr/lib/reviewed.so': 'reviewed-bytes'}
        self.assertIsNone(image_policy.assess(policy, match, evidence))
        evidence['file_sha256'] = {'/usr/lib/reviewed.so': 'reviewed-bytes'}
        self.assertEqual('not_affected_component_absent', image_policy.assess(policy, match, evidence))
        evidence['file_sha256']['/usr/lib/reviewed.so'] = 'changed-bytes'
        self.assertIsNone(image_policy.assess(policy, match, evidence))

    def test_expired_changed_source_or_dishonest_minor_status_fails_closed(self):
        policy, _, _ = self.sample()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'Dockerfile').write_bytes(b'FROM reviewed\n')
            policy.update({'schema_version': 1, 'expires_on': '2026-11-09', 'source_bindings': {'Dockerfile': hashlib.sha256(b'FROM reviewed\n').hexdigest()}})
            path = root / 'policy.json'; path.write_text(json.dumps(policy))
            self.assertTrue(image_policy.load_policy(path, root, today=date(2026, 10, 26)))
            with self.assertRaises(RuntimeError): image_policy.load_policy(path, root, today=date(2026, 11, 10))
            policy['assessments'][0]['vendor_status'] = 'fixed'; path.write_text(json.dumps(policy))
            with self.assertRaises(RuntimeError): image_policy.load_policy(path, root, today=date(2026, 10, 26))
            (root / 'Dockerfile').write_bytes(b'FROM changed\n')
            with self.assertRaises(RuntimeError): image_policy.load_policy(path, root, today=date(2026, 10, 26))

    def test_source_bound_review_survives_calendar_but_new_cve_version_or_elf_still_blocks(self):
        policy, match, evidence = self.sample()
        entry = policy['assessments'][0]
        entry.update(classification='fixed_by_pinned_security_upgrade', vendor_status='fixed',
                     file_sha256={'/lib/reviewed.so': hashlib.sha256(b'patched ELF').hexdigest()})
        evidence['file_sha256'] = dict(entry['file_sha256'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'Dockerfile').write_bytes(b'FROM reviewed\n')
            policy.update(schema_version=2, review_policy='source_and_artifact_bound',
                          source_bindings={'Dockerfile': hashlib.sha256(b'FROM reviewed\n').hexdigest()})
            path = root/'policy.json'; path.write_text(json.dumps(policy))
            verified = image_policy.load_policy(path, root, today=date(2040, 1, 1))
            self.assertEqual('fixed_by_pinned_security_upgrade', image_policy.assess(verified, match, evidence))
            for field, value in [('id', 'CVE-2040-NEW'), ('version', 'new-package-version')]:
                changed = json.loads(json.dumps(match))
                changed['vulnerability' if field=='id' else 'artifact'][field] = value
                self.assertIsNone(image_policy.assess(verified, changed, evidence))
            evidence['file_sha256']['/lib/reviewed.so'] = 'different ELF'
            self.assertIsNone(image_policy.assess(verified, match, evidence))
            entry['classification'] = 'still_vulnerable_vendor_minor'; path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(RuntimeError, 'cannot accept an unfixed'):
                image_policy.load_policy(path, root, today=date(2040, 1, 1))
            entry['classification'] = 'fixed_by_pinned_security_upgrade'; path.write_text(json.dumps(policy))
            (root/'Dockerfile').write_bytes(b'FROM changed\n')
            with self.assertRaisesRegex(RuntimeError, 'source binding changed'):
                image_policy.load_policy(path, root, today=date(2040, 1, 1))


if __name__ == '__main__':
    unittest.main()
