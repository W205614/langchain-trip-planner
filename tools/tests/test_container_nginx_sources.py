import io
import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.container_sources import Collector, alpine_sources, nginx_acme_crate_sources, validate_official_nginx_packages, verify_nginx_index


class OfficialNginxSourceTests(unittest.TestCase):
    def test_apk_zero_exit_with_untrusted_signature_is_not_a_successful_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            collector = Collector(Path(temporary))
            result = subprocess.CompletedProcess([], 0, 'OK: 71 distinct packages available\n',
                                                  'WARNING: opening APKINDEX.tar.gz: UNTRUSTED signature\n')
            with patch('tools.container_sources.subprocess.run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, '(?s)signature verification failed.*UNTRUSTED'):
                    verify_nginx_index(collector, 'image')

    def fixture(self):
        environment = {'NGINX_VERSION': '1.30.6', 'PKG_RELEASE': '1', 'DYNPKG_RELEASE': '1',
                       'NJS_VERSION': '1.0.2', 'NJS_RELEASE': '1', 'ACME_VERSION': '0.4.1'}
        versions = [('nginx', '1.30.6-r1'), ('nginx-module-geoip', '1.30.6-r1'),
                    ('nginx-module-image-filter', '1.30.6-r1'), ('nginx-module-xslt', '1.30.6-r1'),
                    ('nginx-module-njs', '1.30.6.1.0.2-r1'), ('nginx-module-acme', '1.30.6.0.4.1-r1')]
        packages = [{'P': name, 'V': version, 'o': name, 'c': None, 'A': 'x86_64',
                     'm': 'NGINX Packaging <nginx-packaging@f5.com>', 'U': 'https://nginx.org/'}
                    for name, version in versions]
        return packages, environment

    def test_only_exact_reviewed_official_package_group_is_eligible(self):
        packages, environment = self.fixture()
        validate_official_nginx_packages(packages, environment)
        for field, replacement in [('V', '1.30.6-r2'), ('m', 'other vendor'), ('o', 'other origin'),
                                   ('U', 'https://example.invalid/'), ('A', 'aarch64'), ('c', 'invented-aports-commit')]:
            with self.subTest(field=field):
                modified = [dict(row) for row in packages]
                modified[0][field] = replacement
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed NGINX package identity.*nginx'):
                    validate_official_nginx_packages(modified, environment)
        environment['NJS_VERSION'] = 'unreviewed'
        with self.assertRaisesRegex(RuntimeError, 'exact reviewed release'):
            validate_official_nginx_packages(packages, environment)

    def test_missing_or_additional_nginx_modules_are_not_silently_approved(self):
        packages, environment = self.fixture()
        for modified in (packages[:-1], packages + [dict(packages[0], P='nginx-module-extra')]):
            with self.assertRaisesRegex(RuntimeError, 'Unreviewed NGINX package group'):
                validate_official_nginx_packages(modified, environment)

    def test_other_apk_packages_without_a_commit_fail_with_the_specific_object(self):
        installed = b'P: unknown-package\nV: 2.0-r3\no: unknown-origin\nm: Unknown maintainer\n\n'
        with tempfile.TemporaryDirectory() as temporary:
            with patch('tools.container_sources.docker', return_value=installed), \
                    patch('tools.container_sources.busybox_provenance', return_value=({}, {})):
                with self.assertRaisesRegex(RuntimeError, 'unknown-package=2.0-r3.*unknown-origin.*Unknown maintainer'):
                    alpine_sources(Collector(Path(temporary)), 'image', 'frontend')

    def test_acme_unreviewed_dependency_sources_fail_before_any_fetch(self):
        lock = b'version=3\n[[package]]\nname="external"\nversion="1.0.0"\nsource="git+https://example.invalid/repo"\n'
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'acme.tar.gz'
            with tarfile.open(source, 'w:gz') as archive:
                member = tarfile.TarInfo('acme/Cargo.lock')
                member.size = len(lock)
                archive.addfile(member, io.BytesIO(lock))
                member = tarfile.TarInfo('acme/LICENSE')
                member.size = 7
                archive.addfile(member, io.BytesIO(b'Apache\n'))
            collector = Collector(Path(temporary) / 'bundle')
            with self.assertRaisesRegex(RuntimeError, 'Unreviewed NGINX ACME dependency source.*external'):
                nginx_acme_crate_sources(collector, source)
            self.assertEqual(collector.downloads, {})

    def test_missing_crate_license_text_uses_only_the_reviewed_declared_option(self):
        for name in ('http-serde', 'unreviewed-crate'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                collector = Collector(root / 'bundle')
                path = collector.root / f'nginx/crates/{name}-2.1.1.crate'
                path.parent.mkdir(parents=True)
                manifest = f'[package]\nname="{name}"\nversion="2.1.1"\nlicense="Apache-2.0 OR MIT"\nauthors=["Original author"]\n'.encode()
                with tarfile.open(path, 'w:gz') as archive:
                    member = tarfile.TarInfo(f'{name}-2.1.1/Cargo.toml')
                    member.size = len(manifest)
                    archive.addfile(member, io.BytesIO(manifest))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                lock = f'version=3\n[[package]]\nname="{name}"\nversion="2.1.1"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="{digest}"\n'.encode()
                source = root / 'acme.tar.gz'
                with tarfile.open(source, 'w:gz') as archive:
                    for filename, content in {'Cargo.lock': lock, 'LICENSE': b'Unchanged standard terms\n'}.items():
                        member = tarfile.TarInfo('acme/' + filename)
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))
                if name == 'unreviewed-crate':
                    with self.assertRaisesRegex(RuntimeError, 'Missing NGINX ACME crate license text.*unreviewed-crate'):
                        nginx_acme_crate_sources(collector, source)
                else:
                    nginx_acme_crate_sources(collector, source)
                    notices = collector.root / 'licenses/native/nginx-acme-crates/http-serde-2.1.1'
                    self.assertEqual((notices / 'Apache-2.0.txt').read_bytes(), b'Unchanged standard terms\n')
                    self.assertEqual((notices / 'UPSTREAM-Cargo.toml').read_bytes(), manifest)
