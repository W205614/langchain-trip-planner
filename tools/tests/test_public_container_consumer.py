"""Public consumer checks use canonical bytes and immutable image content."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('public_container_consumer', TOOLS / 'public_container_consumer.py')
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


def archive_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class PublicConsumerTests(unittest.TestCase):
    def test_checksum_paths_and_duplicates_rejected(self):
        digest = 'a' * 64
        self.assertEqual(consumer.checksum_entries(f'{digest}  project/file\n'.encode()), {'project/file': digest})
        for names in (['../outside'], ['/absolute'], ['a\\b'], ['same', 'same']):
            with self.subTest(names=names), self.assertRaises(RuntimeError):
                consumer.checksum_entries(''.join(f'{digest}  {name}\n' for name in names).encode())

    def test_hosted_source_uses_canonical_bytes_and_excludes_private_paths(self):
        files = {'LICENSE': b'license\n', 'THIRD_PARTY_NOTICES.md': b'notices\n', 'README.md': b'LF\n'}
        hosted = lambda entries: archive_bytes({'repository-tag/' + name: value for name, value in entries.items()})
        result = consumer.compare_tag_archive(hosted(files), archive_bytes(files))
        self.assertTrue(result['all_file_contents_match_commit'])
        with self.assertRaisesRegex(RuntimeError, 'canonical'):
            consumer.compare_tag_archive(hosted({**files, 'README.md': b'CRLF\r\n'}), archive_bytes(files))
        private = {**files, 'backend/.env': b'synthetic'}
        with self.assertRaisesRegex(RuntimeError, 'Private/runtime'):
            consumer.compare_tag_archive(hosted(private), archive_bytes(private))

    def test_source_bundle_inner_hashes_and_base_binding(self):
        images = {service: {'id': service, 'layers': [service + '-layer']} for service in consumer.SERVICES}
        candidate = {'images': {service: {'base_id': row['id'], 'base_layers': row['layers']} for service, row in images.items()}}
        content = b'upstream bytes'
        project = archive_bytes({'source.txt': b'canonical source'})
        files = {'sources/input': content, 'project/source.txt': b'canonical source', 'manifest.json': json.dumps({
            'images': images, 'downloads': {'sources/input': {'sha256': hashlib.sha256(content).hexdigest()}}}).encode()}
        files['SHA256SUMS'] = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n' for name, value in files.items()).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.zip'
            path.write_bytes(archive_bytes(files))
            self.assertTrue(consumer.verify_source_bundle(path, candidate, project)['zip_crc_passed'])
            with self.assertRaisesRegex(RuntimeError, 'canonical Tag'):
                consumer.verify_source_bundle(path, candidate, archive_bytes({'source.txt': b'different Tag source'}))
            wrong = copy.deepcopy(candidate)
            wrong['images']['agent']['base_id'] = 'wrong'
            with self.assertRaisesRegex(RuntimeError, 'different application base'):
                consumer.verify_source_bundle(path, wrong, project)
            path.write_bytes(archive_bytes({**files, 'sources/input': b'tampered'}))
            with self.assertRaisesRegex(RuntimeError, 'hash differs'):
                consumer.verify_source_bundle(path, candidate, project)

    def test_image_config_digest_is_distinct_from_engine_id_and_mutations_fail(self):
        source = 'a' * 40
        config = {field: None for field in consumer.CONFIG_FIELDS}
        config['Labels'] = {'org.opencontainers.image.revision': source}
        actual = {'Id': 'sha256:engine-id', 'Config': copy.deepcopy(config),
                  'RootFS': {'Layers': ['sha256:layer1', 'sha256:layer2']}, 'Os': 'linux', 'Architecture': 'amd64'}
        expected = {'config_digest': 'sha256:archive-config', 'layers': actual['RootFS']['Layers'][:], 'configuration': config}
        registry = {'config': {'digest': expected['config_digest']}}
        consumer.registry_image_checks(actual, registry, expected, source, 'linux/amd64')
        mutations = [
            ('registry', lambda value: value['config'].update(digest='sha256:wrong')),
            ('actual', lambda value: value['RootFS']['Layers'].reverse()),
            ('actual', lambda value: value['Config'].update(Cmd=['unexpected'])),
            ('actual', lambda value: value['Config']['Labels'].update({'org.opencontainers.image.revision': 'b' * 40})),
            ('actual', lambda value: value.update(Architecture='arm64')),
        ]
        for target, mutation in mutations:
            with self.subTest(target=target):
                changed = {'registry': copy.deepcopy(registry), 'actual': copy.deepcopy(actual)}
                mutation(changed[target])
                with self.assertRaises(RuntimeError):
                    consumer.registry_image_checks(changed['actual'], changed['registry'], expected, source, 'linux/amd64')

    def test_project_source_crlf_equivalence_rejects_other_text_and_binary_changes(self):
        images = {service: {'id': service, 'layers': [service + '-layer']} for service in consumer.SERVICES}
        candidate = {'images': {service: {'base_id': row['id'], 'base_layers': row['layers']} for service, row in images.items()}}
        cases = [
            (b'first\r\nsecond\n', b'first\nsecond\n', True),
            ('中文\r\n'.encode(), '中文\n'.encode(), True),
            (b'changed\r\n', b'original\n', False),
            (b'\0binary\r\n', b'\0binary\n', False),
            (b'\xffbinary\r\n', b'\xffbinary\n', False),
            (b'first\r', b'first\n', False),
            (b'first\r\n', b'first', False),
            (b'\0unchanged\r\n', b'\0unchanged\r\n', True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.zip'
            for bundled, committed, allowed in cases:
                files = {'project/source.txt': bundled,
                         'manifest.json': json.dumps({'images': images, 'downloads': {}}).encode()}
                files['SHA256SUMS'] = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n' for name, value in files.items()).encode()
                path.write_bytes(archive_bytes(files))
                with self.subTest(bundled=bundled, committed=committed):
                    if allowed:
                        result = consumer.verify_source_bundle(path, candidate, archive_bytes({'source.txt': committed}))
                        self.assertEqual(['source.txt'] if bundled != committed else [], result['project_crlf_normalized_files'])
                        self.assertTrue(result['all_inner_hashes_verified'])
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'canonical Tag'):
                            consumer.verify_source_bundle(path, candidate, archive_bytes({'source.txt': committed}))

    def test_consumer_workflow_has_only_read_permissions_and_no_token(self):
        workflow = (TOOLS.parent / '.github/workflows/publish-containers.yml').read_text(encoding='utf-8')
        job = workflow.split('\n  consumer:\n', 1)[1]
        self.assertIn('needs: publish', job)
        self.assertIn('contents: read', job)
        self.assertNotIn('write', job)
        self.assertNotIn('GH_TOKEN', job)
        self.assertNotIn('secrets.', job)
        self.assertIn('ref: ${{ inputs.version }}', job)
        self.assertIn('fetch-depth: 0', job)
        self.assertIn('--source "$GITHUB_SHA"', job)

    def test_standalone_consumer_uses_exact_verifier_and_immutable_tag_source(self):
        workflow = (TOOLS.parent / '.github/workflows/consume-published-containers.yml').read_text(encoding='utf-8')
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertIn('fetch-depth: 0', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('contents: read', workflow)
        self.assertIn("f'refs/tags/{version}^{{commit}}'", workflow)
        self.assertIn('--source "$PUBLISHED_SOURCE"', workflow)
        for forbidden in ('packages: write', 'GH_TOKEN', 'secrets.', 'container_release.py',
                          'needs:', 'push:', 'pull_request:', '--source "$GITHUB_SHA"'):
            self.assertNotIn(forbidden, workflow)
        for pinned in ('actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803',
                       'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1',
                       'actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f'):
            self.assertIn(pinned, workflow)
        resolver = textwrap.dedent(workflow.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
            def git(*args):
                return subprocess.check_output(['git', '-c', 'user.name=consumer-fixture',
                    '-c', 'user.email=consumer@example.invalid', '-c', 'commit.gpgsign=false',
                    '-c', 'tag.gpgSign=false', *args], cwd=root, env=env, stderr=subprocess.PIPE, text=True).strip()
            git('init', '--quiet')
            (root / 'source.txt').write_text('published application source\n', encoding='utf-8')
            git('add', 'source.txt'); git('commit', '--quiet', '-m', 'published source')
            published = git('rev-parse', 'HEAD')
            git('tag', '-a', 'v1.2.3', '-m', 'immutable release')
            (root / 'verifier.txt').write_text('new verifier\n', encoding='utf-8')
            git('add', 'verifier.txt'); git('commit', '--quiet', '-m', 'new verifier')
            verifier = git('rev-parse', 'HEAD')
            output = root / 'workflow-env'
            env.update(VERSION='v1.2.3', GITHUB_SHA=verifier, GITHUB_ENV=str(output))
            result = subprocess.run([sys.executable, '-c', resolver], cwd=root, env=env, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            self.assertNotEqual(published, verifier)
            self.assertEqual('PUBLISHED_SOURCE=' + published + '\n', output.read_text(encoding='utf-8'))
            original = output.read_bytes()
            for version in ('invalid-version', 'v9.9.9'):
                env['VERSION'] = version
                result = subprocess.run([sys.executable, '-c', resolver], cwd=root, env=env, capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(original, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
