"""Distribution calibration must not hide unrelated or still-vulnerable findings."""
from datetime import date
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from tools import image_policy


class DistributionPolicyTests(unittest.TestCase):
    def bound_sample(self, classification):
        policy, match, evidence = self.sample()
        entry = policy['assessments'][0]
        policy.update(schema_version=2, review_policy='source_and_artifact_bound',
                      source_bindings={'review.md': hashlib.sha256(b'reviewed boundary\n').hexdigest()})
        entry.update(classification=classification, review_record='review.md')
        if classification == 'not_affected_execution_path':
            entry['requires_source_lf_sha256'] = {
                '/app/app/main.py': hashlib.sha256(b'embedded = True\n').hexdigest(),
                '/usr/local/lib/python3.13/site-packages/reviewed/client.py': hashlib.sha256(b'client_only = True\n').hexdigest()}
            evidence['source_lf_sha256'] = dict(entry['requires_source_lf_sha256'])
        else:
            entry.update(file_sha256={'/usr/lib/libc.so.6': hashlib.sha256(b'exact ELF').hexdigest()},
                         requires_architecture='amd64')
            evidence.update(file_sha256=dict(entry['file_sha256']), architecture='amd64')
        return policy, match, evidence

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

    def test_execution_path_requires_frozen_review_and_actual_application_and_dependency_sources(self):
        policy, match, evidence = self.bound_sample('not_affected_execution_path')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'review.md').write_bytes(b'reviewed boundary\n')
            path = root/'policy.json'; path.write_text(json.dumps(policy))
            verified = image_policy.load_policy(path, root)
            self.assertEqual('not_affected_execution_path', image_policy.assess(verified, match, evidence))
            for source in evidence['source_lf_sha256']:
                for value in (None, hashlib.sha256(b'changed source').hexdigest()):
                    changed = deepcopy(evidence)
                    if value is None: del changed['source_lf_sha256'][source]
                    else: changed['source_lf_sha256'][source] = value
                    self.assertIsNone(image_policy.assess(verified, match, changed))
            for field in ('review_record', 'requires_source_lf_sha256'):
                invalid = deepcopy(policy); del invalid['assessments'][0][field]
                self.assertIsNone(image_policy.assess(invalid, match, evidence))
                path.write_text(json.dumps(invalid))
                with self.assertRaises(RuntimeError): image_policy.load_policy(path, root)
            invalid = deepcopy(policy); invalid['assessments'][0]['review_record'] = 'unfrozen.md'
            self.assertIsNone(image_policy.assess(invalid, match, evidence))
            invalid = deepcopy(policy)
            del invalid['assessments'][0]['requires_source_lf_sha256']['/app/app/main.py']
            self.assertIsNone(image_policy.assess(invalid, match, evidence))
            (root/'review.md').write_bytes(b'changed review\n'); path.write_text(json.dumps(policy))
            with self.assertRaisesRegex(RuntimeError, 'source binding changed'):
                image_policy.load_policy(path, root)

    def test_configuration_requires_elf_architecture_and_exact_artifact_version(self):
        policy, match, evidence = self.bound_sample('not_affected_build_configuration')
        self.assertEqual('not_affected_build_configuration', image_policy.assess(policy, match, evidence))
        for field, value in [('architecture', 'ppc64le'), ('architecture', None),
                             ('file_sha256', {}), ('packages', {'libc6': 'different revision'})]:
            changed = deepcopy(evidence); changed[field] = value
            self.assertIsNone(image_policy.assess(policy, match, changed))
        for field, value in [('name', 'unrelated-package'), ('version', 'different revision'), ('type', 'python')]:
            changed = deepcopy(match); changed['artifact'][field] = value
            self.assertIsNone(image_policy.assess(policy, changed, evidence))
        changed = deepcopy(match); changed['vulnerability']['id'] = 'raw-unknown-CVE'
        self.assertIsNone(image_policy.assess(policy, changed, evidence))
        for field in ('review_record', 'file_sha256', 'requires_architecture'):
            invalid = deepcopy(policy); del invalid['assessments'][0][field]
            self.assertIsNone(image_policy.assess(invalid, match, evidence))

    def test_empty_btmp_hash_does_not_replace_mode_uid_gid_configuration_evidence(self):
        policy, match, evidence = self.bound_sample('not_affected_build_configuration')
        entry = policy['assessments'][0]
        del entry['requires_architecture']
        entry['file_sha256'] = {'/var/log/btmp': hashlib.sha256(b'').hexdigest(),
                                '/etc/login.defs': hashlib.sha256(b'LOG_UNKFAIL_ENAB no\n').hexdigest()}
        entry['requires_file_metadata'] = {'/var/log/btmp': {'mode': 0o660, 'uid': 0, 'gid': 43}}
        evidence.update(file_sha256=dict(entry['file_sha256']), file_metadata=deepcopy(entry['requires_file_metadata']))
        self.assertEqual('not_affected_build_configuration', image_policy.assess(policy, match, evidence))
        for field, value in [('mode', 0o666), ('uid', 1000), ('gid', 0)]:
            changed = deepcopy(evidence); changed['file_metadata']['/var/log/btmp'][field] = value
            self.assertIsNone(image_policy.assess(policy, match, changed))
            changed = deepcopy(evidence); del changed['file_metadata']['/var/log/btmp'][field]
            self.assertIsNone(image_policy.assess(policy, match, changed))
            invalid = deepcopy(policy); del invalid['assessments'][0]['requires_file_metadata']['/var/log/btmp'][field]
            self.assertIsNone(image_policy.assess(invalid, match, evidence))
        invalid = deepcopy(policy); del invalid['assessments'][0]['file_sha256']['/var/log/btmp']
        self.assertIsNone(image_policy.assess(invalid, match, evidence))
        changed = deepcopy(evidence); changed['file_sha256']['/etc/login.defs'] = hashlib.sha256(b'LOG_UNKFAIL_ENAB yes\n').hexdigest()
        self.assertIsNone(image_policy.assess(policy, match, changed))

    def test_new_classifications_require_schema_two_and_cannot_become_vulnerable_acceptance(self):
        for classification in image_policy.BOUND_NOT_AFFECTED:
            policy, match, evidence = self.bound_sample(classification)
            policy['schema_version'] = 1
            self.assertIsNone(image_policy.assess(policy, match, evidence))
        policy, match, evidence = self.sample()
        acceptance = {'status': 'effective_temporary_acceptance_still_vulnerable', 'allowed_cve_ids': []}
        self.assertIsNone(image_policy.assess(policy, match, evidence, acceptance))

    def test_stopped_inventory_retains_raw_lf_metadata_and_architecture_without_environment_values(self):
        paths = {
            '/var/lib/dpkg/status': b'Package: libc6\nVersion: reviewed\nStatus: install ok installed\n',
            '/usr/lib/os-release': b'ID=debian\nVERSION_ID="13"\n',
            '/app/app/main.py': b'first = 1\r\nsecond = 2\r\n',
            '/var/log/btmp': b''}
        calls = []
        def docker(command, **kwargs):
            calls.append(command)
            if command[1] == 'create':
                return type('Result', (), {'returncode': 0, 'stdout': 'container-fixture\n'})()
            if command[1] == 'cp':
                path = command[-2].split(':', 1)[1]
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode='w') as archive:
                    member = tarfile.TarInfo(Path(path).name)
                    member.size = len(paths[path]); member.mode = 0o660; member.uid = 0; member.gid = 43
                    archive.addfile(member, io.BytesIO(paths[path]))
                return type('Result', (), {'returncode': 0, 'stdout': payload.getvalue()})()
            field = command[-2]
            if field == '{{json .Config.Env}}': value = '["PRIVATE_KEY=never-persist-this-secret"]'
            elif field == '{{json .Architecture}}': value = '"amd64"'
            else: self.fail('Unexpected Docker operation')
            return type('Result', (), {'returncode': 0, 'stdout': value})()
        policy = {'assessments': [{'requires_source_lf_sha256': {'/app/app/main.py': 'requested'},
                                   'requires_file_metadata': {'/var/log/btmp': {}}}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(image_policy.subprocess, 'run', side_effect=docker):
            evidence = image_policy.inventory('sha256:fixture', policy, Path(folder))
            self.assertEqual(hashlib.sha256(paths['/app/app/main.py']).hexdigest(), evidence['file_sha256']['/app/app/main.py'])
            self.assertEqual(hashlib.sha256(b'first = 1\nsecond = 2\n').hexdigest(), evidence['source_lf_sha256']['/app/app/main.py'])
            self.assertEqual({'mode': 0o660, 'uid': 0, 'gid': 43}, evidence['file_metadata']['/var/log/btmp'])
            self.assertEqual('amd64', evidence['architecture'])
            self.assertEqual(['PRIVATE_KEY'], evidence['environment_names'])
            self.assertNotIn('never-persist-this-secret', (Path(folder)/'distribution-inventory.json').read_text())
            self.assertFalse(any(command[1] in ('start', 'run', 'rm', 'exec') for command in calls))

    def test_export_resolves_parent_and_soname_links_without_host_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rootfs.tar'
            with tarfile.open(path, 'w') as archive:
                for name, target in [('lib', 'usr/lib'),
                                     ('usr/lib/libreviewed.so.1', 'libreviewed.so.1.2')]:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE; member.linkname = target
                    archive.addfile(member)
                member = tarfile.TarInfo('usr/lib/libreviewed.so.1.2')
                member.size = 8; member.mode = 0o640; member.uid = 7; member.gid = 8
                archive.addfile(member, io.BytesIO(b'ELFbytes'))
            result = image_policy.exported_requested_files(path, ['/lib/libreviewed.so.1', '/usr/bin/removed'])
            self.assertEqual((b'ELFbytes', {'mode': 0o640, 'uid': 7, 'gid': 8}), result['/lib/libreviewed.so.1'])
            self.assertIsNone(result['/usr/bin/removed'])
            self.assertEqual(['rootfs.tar'], [p.name for p in Path(folder).iterdir()])

    def test_docker_cp_not_found_needs_stopped_export_confirmation(self):
        paths = {'/var/lib/dpkg/status': b'Package: fixture\nVersion: 1\nStatus: install ok installed\n',
                 '/usr/lib/os-release': b'ID=debian\nVERSION_ID="13"\n'}
        calls = []
        def docker(command, **kwargs):
            calls.append(command)
            if command[1] == 'create':
                return type('Result', (), {'returncode': 0, 'stdout': 'container-fixture\n'})()
            if command[1] == 'cp':
                requested = command[-2].split(':', 1)[1]
                if requested not in paths:
                    return type('Result', (), {'returncode': 1, 'stderr': b'Could not find the file'})()
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode='w') as archive:
                    member = tarfile.TarInfo(Path(requested).name); member.size = len(paths[requested])
                    archive.addfile(member, io.BytesIO(paths[requested]))
                return type('Result', (), {'returncode': 0, 'stdout': payload.getvalue()})()
            if command[1] == 'export':
                with tarfile.open(command[3], 'w') as archive:
                    member = tarfile.TarInfo('usr/lib/present.so'); member.size = 7
                    archive.addfile(member, io.BytesIO(b'present'))
                    member = tarfile.TarInfo('usr/lib/alias.so'); member.type = tarfile.SYMTYPE
                    member.linkname = 'present.so'; archive.addfile(member)
                return type('Result', (), {'returncode': 0})()
            value = '[]' if command[-2] == '{{json .Config.Env}}' else '"amd64"'
            return type('Result', (), {'returncode': 0, 'stdout': value})()
        policy = {'assessments': [{'absent_paths': ['/usr/lib/alias.so', '/usr/bin/removed']}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(image_policy.subprocess, 'run', side_effect=docker):
            evidence = image_policy.inventory('sha256:fixture', policy, Path(folder))
            self.assertEqual(['/usr/bin/removed'], evidence['absent_paths'])
            self.assertEqual(hashlib.sha256(b'present').hexdigest(), evidence['file_sha256']['/usr/lib/alias.so'])
            self.assertEqual(1, sum(command[1] == 'export' for command in calls))
            self.assertFalse(any(command[1] in ('start', 'run', 'exec', 'rm') for command in calls))

    def test_expanding_image_link_is_not_misreported_as_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rootfs.tar'
            with tarfile.open(path, 'w') as archive:
                member = tarfile.TarInfo('loop'); member.type = tarfile.SYMTYPE
                member.linkname = 'loop/child'; archive.addfile(member)
            with self.assertRaisesRegex(RuntimeError, 'Too many image export link traversals'):
                image_policy.exported_requested_files(path, ['/loop/file'])


if __name__ == '__main__':
    unittest.main()
