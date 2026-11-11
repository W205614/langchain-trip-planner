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


class AlpineBackportTests(unittest.TestCase):
    APK = ('C:Q1example\nP:musl\nV:1.2.6-r3\nA:x86_64\nT:libc\n'
           'F:lib\nR:libc.musl-x86_64.so.1\na:0:0:755\nR:ld-musl-x86_64.so.1\n\n'
           'P:ca-certificates-bundle\nV:20261109-r0\nA:noarch\nF:etc/ssl\nR:cert.pem\n')

    def sample(self):
        path = '/usr/local/bin/python3.13'
        library = '/usr/local/lib/libpython3.13.so.1.0'
        entry = {'distribution': 'alpine', 'release': '3.24.4', 'artifact_type': 'binary',
                 'artifact_versions': {'python': '3.13.18'},
                 'requires_apk': {'musl': '1.2.6-r3', 'ca-certificates-bundle': '20261109-r0'},
                 'requires_apk_architectures': {'musl': 'x86_64', 'ca-certificates-bundle': 'noarch'},
                 'requires_architecture': 'amd64', 'ids': ['CVE-fixture'],
                 'classification': 'fixed_by_pinned_security_upgrade',
                 'references': ['https://github.com/python/cpython'], 'reason': 'Complete reviewed backport',
                 'review_record': 'review.md', 'file_sha256': {path: hashlib.sha256(b'\x7fELFreviewed').hexdigest(),
                                                             library: hashlib.sha256(b'\x7fELFlibrary').hexdigest()},
                 'requires_elf_paths': [path, library],
                 'requires_artifact_paths': deepcopy(image_policy.APK_RECEIVER_ARTIFACT_PATHS),
                 'requires_image_configuration': deepcopy(image_policy.APK_RECEIVER_CONFIGURATION)}
        entry['file_sha256'].update({
            image_policy.APK_RECEIVER_SOCKET: hashlib.sha256(b'\x7fELFsocket').hexdigest(),
            '/usr/local/lib/python3.13/http/client.py': hashlib.sha256(b'# reviewed HTTP\n').hexdigest(),
            '/usr/local/lib/python3.13/poplib.py': hashlib.sha256(b'# reviewed POP3\n').hexdigest()})
        entry['requires_elf_paths'].append(image_policy.APK_RECEIVER_SOCKET)
        policy = {'schema_version': 2, 'review_policy': 'source_and_artifact_bound',
                  'source_bindings': {'review.md': hashlib.sha256(b'reviewed\n').hexdigest()}, 'assessments': [entry]}
        match = {'artifact': {'type': 'binary', 'name': 'python', 'version': '3.13.18'},
                 'vulnerability': {'id': 'CVE-fixture'}}
        match['artifact']['locations'] = [dict(path=p, accessPath=p, annotations={'evidence': role})
                                          for role, p in image_policy.APK_RECEIVER_ARTIFACT_PATHS.items()]
        evidence = {'distribution': 'alpine', 'release': '3.24.4', 'architecture': 'amd64',
                    'package_manager': 'apk', 'apk_architecture': 'x86_64',
                    'packages': deepcopy(entry['requires_apk']), 'apk_package_architectures': deepcopy(entry['requires_apk_architectures']),
                    'file_sha256': deepcopy(entry['file_sha256']), 'elf_paths': deepcopy(entry['requires_elf_paths']),
                    'image_configuration': deepcopy(image_policy.APK_RECEIVER_CONFIGURATION)}
        return policy, match, evidence

    def test_installed_apk_records_preserve_versions_and_each_package_architecture(self):
        packages, architectures = image_policy.apk_inventory(self.APK, 'x86_64')
        self.assertEqual({'musl': '1.2.6-r3', 'ca-certificates-bundle': '20261109-r0'}, packages)
        self.assertEqual({'musl': 'x86_64', 'ca-certificates-bundle': 'noarch'}, architectures)
        invalid = ['', self.APK.replace('A:x86_64', 'A:aarch64'),
                   self.APK.replace('V:1.2.6-r3\n', ''),
                   self.APK.replace('P:musl\n', 'P:musl\nP:other\n'),
                   self.APK.replace('A:x86_64\n', 'A:x86_64\nA:x86_64\n'),
                   self.APK + '\nP:musl\nV:other\nA:x86_64\n',
                   self.APK + '\nopaque undecodable record\n', self.APK + '\nX:unknown field\n',
                   self.APK.replace('P:musl', 'P:bad name')]
        for text in invalid:
            with self.subTest(text=text[:45]), self.assertRaises(RuntimeError):
                image_policy.apk_inventory(text, 'x86_64')
        with self.assertRaises(RuntimeError): image_policy.apk_inventory(self.APK, 'aarch64')

    def test_valid_dot_prefixed_virtual_packages_parse_but_change_the_frozen_inventory(self):
        virtual = (self.APK + '\nP:.python-rundeps\nV:20261110.223300\nA:noarch\n'
                   'T:virtual meta package\nD:musl\n\nP:.build-deps\nV:20261110.223301\nA:noarch\n')
        packages, architectures = image_policy.apk_inventory(virtual, 'x86_64')
        self.assertEqual('20261110.223300', packages['.python-rundeps'])
        self.assertEqual('noarch', architectures['.build-deps'])
        policy, match, evidence = self.sample()
        evidence.update(packages=packages, apk_package_architectures=architectures)
        self.assertIsNone(image_policy.assess(policy, match, evidence))

    def test_exact_alpine_identity_rejects_duplicate_opaque_or_conflicting_releases(self):
        raw = b'NAME="Alpine Linux"\nID=alpine\nVERSION_ID="3.24.4"\n'
        self.assertEqual('3.24.4', image_policy.alpine_release_identity(raw, b'3.24.4\n')['VERSION_ID'])
        for changed, release in [(raw + b'ID=alpine\n', b'3.24.4'),
                                 (raw.replace(b'alpine', b'debian'), b'3.24.4'),
                                 (raw, b'3.24.3'), (raw.replace(b'"3.24.4"', b'"3.24.4'), b'3.24.4'),
                                 (raw + b'opaque\n', b'3.24.4')]:
            with self.subTest(raw=changed), self.assertRaises(RuntimeError):
                image_policy.alpine_release_identity(changed, release)

    def test_alpine_backport_is_exact_source_binary_apk_arch_elf_and_receiver_bound(self):
        policy, match, evidence = self.sample()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'review.md').write_bytes(b'reviewed\n')
            path = root / 'policy.json'; path.write_text(json.dumps(policy))
            loaded = image_policy.load_policy(path, root)
            self.assertEqual('fixed_by_pinned_security_upgrade', image_policy.assess(loaded, match, evidence))
            (root / 'review.md').write_bytes(b'changed source review\n')
            with self.assertRaisesRegex(RuntimeError, 'source binding changed'):
                image_policy.load_policy(path, root)
        for field, value in [('package_manager', 'dpkg'), ('package_manager', None),
                             ('apk_architecture', 'aarch64'), ('architecture', 'arm64'), ('release', '3.24.3'),
                             ('packages', {'musl': 'changed'}), ('apk_package_architectures', {'musl': 'noarch'}),
                             ('file_sha256', {}), ('elf_paths', []), ('image_configuration', {'User': '0'})]:
            changed = deepcopy(evidence); changed[field] = value
            self.assertIsNone(image_policy.assess(policy, match, changed), field)
        for field, value in [('name', 'other'), ('version', '3.13.19'), ('type', 'apk')]:
            changed = deepcopy(match); changed['artifact'][field] = value
            self.assertIsNone(image_policy.assess(policy, changed, evidence))
        changed = deepcopy(match); changed['vulnerability']['id'] = 'CVE-new'
        self.assertIsNone(image_policy.assess(policy, changed, evidence))

    def test_missing_mixed_or_broad_apk_policy_requirements_are_rejected(self):
        policy, _, _ = self.sample()
        variants = []
        for key in ('requires_apk', 'requires_architecture', 'requires_apk_architectures',
                    'requires_elf_paths', 'requires_artifact_paths', 'requires_image_configuration', 'review_record'):
            changed = deepcopy(policy); del changed['assessments'][0][key]; variants.append(changed)
        for key, value in [('requires_dpkg', {'libc6': 'reviewed'}), ('artifact_type', 'apk'),
                           ('release', '3.24'), ('requires_apk_architectures', {'musl': 'noarch'}),
                           ('requires_elf_paths', ['/unreviewed.so']),
                           ('requires_image_configuration', {'Entrypoint': ['sh'], 'User': '1000:1000'}),
                           ('classification', 'not_affected_component_absent')]:
            changed = deepcopy(policy); changed['assessments'][0][key] = value; variants.append(changed)
        changed = deepcopy(policy); entry = changed['assessments'][0]
        entry['requires_dpkg'] = entry.pop('requires_apk'); variants.append(changed)
        changed = deepcopy(policy); entry = deepcopy(changed['assessments'][0])
        entry.update(distribution='debian', release='13', requires_dpkg={'libc6': 'reviewed'})
        del entry['requires_apk']; changed['assessments'].append(entry); variants.append(changed)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'review.md').write_bytes(b'reviewed\n'); path = root / 'policy.json'
            for index, variant in enumerate(variants):
                path.write_text(json.dumps(variant))
                with self.subTest(index=index), self.assertRaises(RuntimeError):
                    image_policy.load_policy(path, root)

    def test_same_version_python_at_other_missing_or_ambiguous_locations_is_not_fixed(self):
        policy, match, evidence = self.sample()
        variants = []
        changed = deepcopy(match); del changed['artifact']['locations']; variants.append(changed)
        changed = deepcopy(match); changed['artifact']['locations'].pop(); variants.append(changed)
        changed = deepcopy(match); changed['artifact']['locations'].append(deepcopy(changed['artifact']['locations'][0])); variants.append(changed)
        for field in ('path', 'accessPath'):
            for index in (0, 1):
                changed = deepcopy(match); changed['artifact']['locations'][index][field] = '/unreviewed/python3.13'; variants.append(changed)
        changed = deepcopy(match); changed['artifact']['locations'][1]['annotations']['evidence'] = 'primary'; variants.append(changed)
        for index, changed in enumerate(variants):
            with self.subTest(variant=index):
                self.assertIsNone(image_policy.assess(policy, changed, evidence))

    def test_each_actual_patch_carrier_and_socket_elf_is_mandatory(self):
        policy, match, evidence = self.sample()
        carriers = image_policy.APK_RECEIVER_PATCH_FILES | set(image_policy.APK_RECEIVER_ARTIFACT_PATHS.values())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'review.md').write_bytes(b'reviewed\n'); path = root / 'policy.json'
            for carrier in carriers:
                invalid = deepcopy(policy); del invalid['assessments'][0]['file_sha256'][carrier]
                # Removing its ELF declaration too must not weaken the mandatory contract.
                invalid['assessments'][0]['requires_elf_paths'] = [p for p in invalid['assessments'][0]['requires_elf_paths'] if p != carrier]
                path.write_text(json.dumps(invalid))
                with self.subTest(carrier=carrier), self.assertRaises(RuntimeError):
                    image_policy.load_policy(path, root)
                self.assertIsNone(image_policy.assess(invalid, match, evidence))
                for observed in (None, '0' * 64):
                    changed = deepcopy(evidence)
                    if observed is None:
                        changed['file_sha256'].pop(carrier)
                    else:
                        changed['file_sha256'][carrier] = observed
                    with self.subTest(carrier=carrier, observed=observed):
                        self.assertIsNone(image_policy.assess(policy, match, changed))
                if carrier in evidence['elf_paths']:
                    changed = deepcopy(evidence); changed['elf_paths'].remove(carrier)
                    with self.subTest(carrier=carrier, elf_magic_missing=True):
                        self.assertIsNone(image_policy.assess(policy, match, changed))
            invalid = deepcopy(policy); invalid['assessments'][0]['requires_elf_paths'].remove(image_policy.APK_RECEIVER_SOCKET)
            path.write_text(json.dumps(invalid))
            with self.assertRaises(RuntimeError): image_policy.load_policy(path, root)
            self.assertIsNone(image_policy.assess(invalid, match, evidence))

    def inventory_fixture(self, extra=None, architecture='amd64', database=None):
        calls = []
        files = {'usr/lib/os-release': b'ID=alpine\nVERSION_ID="3.24.4"\n',
                 'etc/alpine-release': b'3.24.4\n',
                 'lib/apk/db/installed': (database or self.APK).encode(),
                 'usr/local/bin/python3.13': b'\x7fELFreviewed',
                 'usr/local/lib/libpython3.13.so.1.0': b'\x7fELFlibrary',
                 image_policy.APK_RECEIVER_SOCKET.lstrip('/'): b'\x7fELFsocket',
                 'usr/local/lib/python3.13/http/client.py': b'# reviewed HTTP\n',
                 'usr/local/lib/python3.13/poplib.py': b'# reviewed POP3\n'}
        files.update(extra or {})
        def docker(command, **kwargs):
            calls.append(command)
            if command[1] == 'create':
                return type('Result', (), {'returncode': 0, 'stdout': 'container-fixture\n'})()
            if command[1] == 'export':
                with tarfile.open(command[3], 'w') as archive:
                    link = tarfile.TarInfo('etc/os-release'); link.type = tarfile.SYMTYPE
                    link.linkname = '../usr/lib/os-release'; archive.addfile(link)
                    for name, raw in files.items():
                        member = tarfile.TarInfo(name); member.size = len(raw)
                        archive.addfile(member, io.BytesIO(raw))
                return type('Result', (), {'returncode': 0})()
            if command[1:3] == ['image', 'inspect']:
                field = command[-2]
                if field == '{{json .Config.Env}}': value = '["PRIVATE_KEY=must-not-persist"]'
                elif field == '{{json .Architecture}}': value = json.dumps(architecture)
                else: value = json.dumps(image_policy.APK_RECEIVER_CONFIGURATION)
                return type('Result', (), {'returncode': 0, 'stdout': value})()
            self.fail('Unexpected image operation: ' + command[1])
        return docker, calls

    def test_stopped_apk_export_resolves_os_release_and_records_exact_files_without_execution(self):
        policy, match, _ = self.sample()
        docker, calls = self.inventory_fixture()
        with tempfile.TemporaryDirectory() as folder, patch.object(image_policy.subprocess, 'run', side_effect=docker):
            evidence = image_policy.inventory('sha256:fixture', policy, Path(folder))
            self.assertEqual('apk', evidence['package_manager'])
            self.assertEqual('x86_64', evidence['apk_package_architectures']['musl'])
            self.assertEqual('noarch', evidence['apk_package_architectures']['ca-certificates-bundle'])
            self.assertIn('/var/lib/dpkg/status', evidence['absent_paths'])
            self.assertEqual('fixed_by_pinned_security_upgrade', image_policy.assess(policy, match, evidence))
            self.assertNotIn('must-not-persist', (Path(folder) / 'distribution-inventory.json').read_text())
            self.assertFalse(any(c[1] in ('run', 'start', 'exec', 'rm', 'cp') for c in calls))

    def test_wrong_manager_arch_or_unparseable_apk_export_is_not_component_absence(self):
        policy, _, _ = self.sample()
        for extra, arch, database in [({'var/lib/dpkg/status': b'Package: libc6\n'}, 'amd64', None),
                                      ({}, 'arm64', None), ({}, 'amd64', 'P:incomplete\n')]:
            docker, calls = self.inventory_fixture(extra, arch, database)
            with self.subTest(arch=arch, database=database), tempfile.TemporaryDirectory() as folder, \
                 patch.object(image_policy.subprocess, 'run', side_effect=docker), self.assertRaises(RuntimeError):
                image_policy.inventory('sha256:fixture', policy, Path(folder))


if __name__ == '__main__':
    unittest.main()
