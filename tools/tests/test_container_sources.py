import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from tools.container_sources import Collector, PROJECT_SOURCE_PATHS, alpine_recipe_sources, busybox_provenance, copy_project_sources, glibc_hardening_provenance, paragraphs, verify_busybox_recipe


class SourceCollectionTests(unittest.TestCase):
    def test_busybox_runtime_cannot_supply_an_archive_different_from_the_canonical_input(self):
        canonical = Path(__file__).resolve().parents[2] / 'deploy/images/busybox-backport'
        lock = dict(line.split('=', 1) for line in (canonical / 'source.lock').read_text().splitlines())
        packages = [{'P': name, 'V': lock['PACKAGE_VERSION'] + '-r0', 'o': 'busybox'}
                    for name in ('busybox', 'busybox-binsh', 'ssl_client')]

        def image_content(image, *args):
            self.assertEqual(args[0], 'cat')
            name = args[1].rsplit('/', 1)[1]
            if name in ('source.lock', 'CVE-2025-60876.patch'):
                return (canonical / name).read_bytes()
            if name == 'aports-busybox.tar.gz':
                return b'changed original source archive'
            return b'fixture metadata'

        with tempfile.TemporaryDirectory() as temporary:
            collector = Collector(Path(temporary))
            with patch('tools.container_sources.docker', side_effect=image_content):
                with self.assertRaisesRegex(RuntimeError, 'source provenance differs.*aports-busybox'):
                    busybox_provenance(collector, 'immutable-image', 'fixture', packages)
            self.assertFalse((collector.root / 'runtime-provenance').exists())

    def test_vendored_alpine_recipe_collection_uses_local_bytes_and_preserves_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'aports-busybox.tar.gz'
            local_input = b'original Alpine input\n'
            recipe = ('pkgver=1.37.0\nsha512sums="\n' + hashlib.sha512(local_input).hexdigest()
                      + '  original.patch\n"\n').encode()
            with tarfile.open(source, 'w:gz') as archive:
                for name, content in {'APKBUILD': recipe, 'original.patch': local_input}.items():
                    entry = tarfile.TarInfo('aports/main/busybox/' + name)
                    entry.size = len(content)
                    archive.addfile(entry, io.BytesIO(content))

            def local_only(request, **kwargs):
                self.assertTrue(request.full_url.startswith('file:'), request.full_url)
                return urlopen(request, **kwargs)

            collector = Collector(root / 'bundle')
            with patch('tools.container_sources.urlopen', side_effect=local_only):
                files = alpine_recipe_sources(collector, 'busybox', 'a' * 40,
                                              hashlib.sha256(source.read_bytes()).hexdigest(), source)
            self.assertEqual(files['APKBUILD'], recipe)
            record, = collector.downloads.values()
            self.assertEqual(record['acquisition'], 'vendored_project_input')
            self.assertTrue(record['url'].startswith('https://gitlab.alpinelinux.org/'))
            self.assertNotIn('file:', record['url'])
            self.assertEqual(record['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())

    def glibc_fixture(self, root):
        canonical = root / 'backend/security/glibc-hardening'
        canonical.mkdir(parents=True)
        source = {'runtime-check.sh': b'check installed runtime\n',
                  'upstream-tests.txt': b'nptl/test-thread\nmalloc/test-arena\n',
                  'test.c': b'actual regression source\n', 'tls.patch': b'reviewed TLS patch\n'}
        for name, content in source.items():
            (canonical / name).write_bytes(content)

        def sums(files):
            return ''.join(hashlib.sha256(content).hexdigest() + '  ' + name + '\n'
                           for name, content in sorted(files.items())).encode()

        source['tests.sha256'] = sums({name: source[name] for name in ('runtime-check.sh', 'upstream-tests.txt', 'test.c')})
        source['hardening-patches.sha256'] = sums({'tls.patch': source['tls.patch']})
        for name in ('tests.sha256', 'hardening-patches.sha256'):
            (canonical / name).write_bytes(source[name])
        elf_hashes = {name: hashlib.sha256(name.encode()).hexdigest()
                      for name in ('libc.so.6', 'ld-linux-x86-64.so.2', 'libresolv.so.2')}
        summary = {'result': 'passed', 'packaged_libc': 'libc6_2.41-12+deb13u4+trip4_amd64.deb',
                   'packaged_elf_sha256': elf_hashes, 'upstream_nptl_pass': 1, 'upstream_malloc_pass': 1,
                   'upstream_results': ['PASS: nptl/test-thread', 'PASS: malloc/test-arena']}
        proof = {name: name.encode() for name in ('tls-guard-regression', 'stack-randomization-regression',
                                                 'arena-regression', 'stack-independent-lifecycle')}
        proof.update({'runtime-check.sh': source['runtime-check.sh'],
                      'summary.json': json.dumps(summary).encode(), 'RUN-CHECKS.txt': b'runtime instructions\n',
                      'SOURCE-INPUTS.sha256': sums(source),
                      'packaged-elf-sha256.txt': ''.join(digest + '  /usr/lib/x86_64-linux-gnu/' + name + '\n'
                                                       for name, digest in elf_hashes.items()).encode()})
        proof['SHA256SUMS'] = sums(proof)

        def transport(image, *args):
            if args[0] == 'dpkg-query':
                return b'2.41-12+deb13u4+trip4'
            if args[0] == 'cat':
                return proof[args[1].rsplit('/', 1)[1]]
            if args[0] in ('sh', 'sha256sum'):
                return b'fixture checksum command accepted\n'
            self.fail('Unexpected container command: ' + str(args))

        return proof, transport

    def test_glibc_proof_binds_sources_and_elf_checks_without_copying_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof, transport = self.glibc_fixture(root)
            collector = Collector(root / 'bundle')
            with patch('tools.container_sources.docker', side_effect=transport) as command:
                record = glibc_hardening_provenance(collector, 'immutable-image', root)
            self.assertEqual(record['status'], 'verified_provenance')
            self.assertFalse(record['runtime_checks_executed_by_collector'])
            folder = collector.root / 'runtime-provenance/agent/glibc-hardening'
            self.assertEqual((folder / 'SOURCE-INPUTS.sha256').read_bytes(), proof['SOURCE-INPUTS.sha256'])
            self.assertFalse((folder / 'arena-regression').exists())
            self.assertFalse((collector.root / 'project').exists())
            command.assert_any_call('immutable-image', 'sha256sum', '-c',
                                    '/usr/local/share/tripplanner/glibc-hardening/packaged-elf-sha256.txt')
            command.assert_any_call('immutable-image', 'sh', '-c',
                                    'cd /usr/local/share/tripplanner/glibc-hardening/ && sha256sum -c SHA256SUMS')

    def test_glibc_trip3_does_not_claim_or_require_new_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('tools.container_sources.docker', return_value=b'2.41-12+deb13u4+trip3') as command:
                record = glibc_hardening_provenance(Collector(root / 'bundle'), 'old-image', root)
            self.assertFalse(record['required'])
            self.assertEqual(record['status'], 'not_required_for_this_package_revision')
            self.assertEqual(command.call_count, 1)
            self.assertFalse((root / 'bundle').exists())

    def test_glibc_trip4_missing_or_failed_hash_proof_is_blocking(self):
        for failed_command in ('cat', 'sh', 'sha256sum'):
            with self.subTest(failed_command=failed_command), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                proof, transport = self.glibc_fixture(root)

                def fail_at(image, *args):
                    if args[0] == failed_command:
                        raise subprocess.CalledProcessError(1, [args[0]])
                    return transport(image, *args)

                collector = Collector(root / 'bundle')
                with patch('tools.container_sources.docker', side_effect=fail_at):
                    with self.assertRaises(subprocess.CalledProcessError):
                        glibc_hardening_provenance(collector, 'immutable-image', root)
                self.assertFalse((collector.root / 'runtime-provenance').exists())

    def test_glibc_self_consistent_proof_cannot_bind_different_sources_or_package(self):
        for change in ('changed-source', 'missing-source-entry', 'wrong-package'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                proof, transport = self.glibc_fixture(root)
                if change == 'changed-source':
                    (root / 'backend/security/glibc-hardening/tls.patch').write_bytes(b'different reviewed TLS order')
                    message = 'Canonical glibc source checksum mismatch'
                elif change == 'missing-source-entry':
                    proof['SOURCE-INPUTS.sha256'] = b''
                    replacement_digest = hashlib.sha256(proof['SOURCE-INPUTS.sha256']).hexdigest()
                    lines = proof['SHA256SUMS'].decode().splitlines()
                    proof['SHA256SUMS'] = ('\n'.join(replacement_digest + '  SOURCE-INPUTS.sha256' if line.endswith('  SOURCE-INPUTS.sha256') else line
                                                       for line in lines) + '\n').encode()
                    message = 'Installed glibc source inputs differ'
                else:
                    summary = json.loads(proof['summary.json'])
                    summary['packaged_libc'] = 'libc6_2.41-12+deb13u4+trip3_amd64.deb'
                    proof['summary.json'] = json.dumps(summary).encode()
                    replacement_digest = hashlib.sha256(proof['summary.json']).hexdigest()
                    lines = proof['SHA256SUMS'].decode().splitlines()
                    proof['SHA256SUMS'] = ('\n'.join(replacement_digest + '  summary.json' if line.endswith('  summary.json') else line
                                                       for line in lines) + '\n').encode()
                    message = 'glibc proof summary differs'
                with patch('tools.container_sources.docker', side_effect=transport):
                    with self.assertRaisesRegex(RuntimeError, message):
                        glibc_hardening_provenance(Collector(root / 'bundle'), 'immutable-image', root)

    def test_hardening_bundle_copies_new_build_inputs_and_nested_tests_dynamically(self):
        relative = 'backend/security/glibc-hardening'
        self.assertIn(relative, PROJECT_SOURCE_PATHS)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'repo' / relative
            (source / 'tests').mkdir(parents=True)
            files = {'stack-randomization.h': b'LGPL source header\n',
                     'check-hardening.sh': b'build and check entry point\n',
                     'tests/later-added-regression.c': b'future real regression input\n'}
            for name, content in files.items():
                (source / name).write_bytes(content)
            (source / '__pycache__').mkdir()
            (source / '__pycache__/build.cpython-313.pyc').write_bytes(b'generated bytecode')
            (source / 'tests/regression.pyc').write_bytes(b'generated bytecode')
            collector = Collector(root / 'bundle')
            with patch('tools.container_sources.PROJECT_SOURCE_PATHS', (relative,)):
                copy_project_sources(collector, root / 'repo')
            for name, content in files.items():
                self.assertEqual((collector.root / 'project' / relative / name).read_bytes(), content)
            self.assertFalse((collector.root / 'project' / relative / '__pycache__').exists())
            self.assertFalse((collector.root / 'project' / relative / 'tests/regression.pyc').exists())

    def test_downstream_busybox_preserves_all_original_inputs_and_checks_the_new_patch(self):
        patch = b'upstream security patch'
        original = 'pkgver=1.37.0\nsha512sums="\n' + 'a' * 128 + '  source.tar.bz2\n' + 'b' * 128 + '  prior-security.patch\n"\n'
        modified = original.replace('pkgver=1.37.0\n', 'pkgver=1.37.0_p20261110\n').replace(
            '\n"\n', '\n' + hashlib.sha512(patch).hexdigest() + '  CVE-2025-60876.patch\n"\n')
        verify_busybox_recipe(original, modified, patch, '1.37.0_p20261110')
        with self.assertRaisesRegex(RuntimeError, 'inputs differ'):
            verify_busybox_recipe(original, modified.replace('b' * 128 + '  prior-security.patch\n', ''), patch, '1.37.0_p20261110')
        with self.assertRaisesRegex(RuntimeError, 'inputs differ'):
            verify_busybox_recipe(original, modified, b'different patch', '1.37.0_p20261110')
        with self.assertRaisesRegex(RuntimeError, 'inputs differ'):
            verify_busybox_recipe(original, modified, patch, '1.37.0_unreviewed')

    def test_distribution_multiline_checksums_and_epochs_survive_parsing(self):
        row, = paragraphs('Package: zlib\nVersion: 1:1.3-1\nChecksums-Sha256:\n abc 12 file.tar\n def 4 file.dsc\n')
        self.assertEqual(row['Version'], '1:1.3-1')
        self.assertEqual(row['Checksums-Sha256'].split(), ['abc', '12', 'file.tar', 'def', '4', 'file.dsc'])

    def test_local_source_checksum_and_windows_epoch_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'original'
            source.write_bytes(b'upstream source')
            collector = Collector(root / 'bundle')
            target = collector.fetch(source.as_uri(), 'indexes/zlib-1:1.3.json', hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(target.name, 'zlib-1%3A1.3.json')
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_corrupt_download_is_not_promoted_or_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'original'
            source.write_bytes(b'truncated')
            collector = Collector(root / 'bundle')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                collector.fetch(source.as_uri(), 'source.tar', '0' * 64)
            self.assertFalse((root / 'bundle/source.tar').exists())
            self.assertEqual(collector.downloads, {})

    def test_external_output_path_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            collector = Collector(Path(temporary))
            with self.assertRaisesRegex(ValueError, 'escapes'):
                collector.fetch('https://example.invalid/source', '../outside.tar')

    def test_truncated_http_body_resumes_and_still_requires_the_full_checksum(self):
        content = b'actual source bytes' * 100
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                header = self.headers.get('Range')
                requests.append(header)
                offset = int(header.removeprefix('bytes=').removesuffix('-')) if header else 0
                self.send_response(206 if header else 200)
                self.send_header('Content-Length', str(len(content) - offset))
                if header:
                    self.send_header('Content-Range', f'bytes {offset}-{len(content)-1}/{len(content)}')
                self.end_headers()
                self.wfile.write(content[offset:] if header else content[:100])
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                collector = Collector(Path(temporary))
                path = collector.fetch(f'http://127.0.0.1:{server.server_port}/source', 'source.tar', hashlib.sha256(content).hexdigest())
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(requests, [None, 'bytes=100-'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
