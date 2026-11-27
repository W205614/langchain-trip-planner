import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from container_release import verify_release_version


class ContainerPublicationGuards(unittest.TestCase):
    def command(self, version, directory, **environment):
        env = {**os.environ, 'CI': '', 'GITHUB_REPOSITORY': '', 'GH_TOKEN': '', **environment}
        return subprocess.run([sys.executable, 'tools/container_release.py', '--version', version,
                               'promote', '--directory', str(directory)], env=env, capture_output=True, text=True)

    def test_local_execution_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.command('v0.1.3', directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('requires this repository Actions workflow', result.stderr)

    def test_shell_like_version_is_rejected_before_any_candidate_read(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.command('v0.1.3;echo unexpected', directory)
            self.assertEqual(result.returncode, 2)
            self.assertIn('version must be vX.Y.Z', result.stderr)

    def test_mismatched_source_is_rejected_before_registry_or_github_access(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'container-manifest.json').write_text(json.dumps({
                'source': 'a' * 40, 'version': 'v0.1.3', 'images': dict.fromkeys(('agent', 'backend', 'frontend'))}))
            result = self.command('v0.1.3', directory, CI='true', GITHUB_REPOSITORY='W205614/langchain-trip-planner',
                                  GH_TOKEN='synthetic-never-send', GITHUB_SHA='b' * 40)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Candidate version/source mismatch', result.stderr)


class ReleaseVersionChecks(unittest.TestCase):
    version = 'v2.3.4'
    notes = (
        'git clone --branch v2.3.4 https://github.com/W205614/langchain-trip-planner.git\n'
        'TRIP_VERSION=v2.3.4\n'
    )
    readme = (
        '当前正式版本：[v2.3.4](https://github.com/W205614/langchain-trip-planner/releases/tag/v2.3.4)\n'
        + notes
    )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'README.md').write_text(self.readme, encoding='utf-8')
        for name in ('docker-compose.yml', 'docker-compose.images.yml'):
            shutil.copyfile(TOOLS.parent / name, self.root / name)

    def test_resolves_all_three_images_with_matching_docs_and_crlf_release_notes(self):
        verify_release_version(self.root, self.version, self.notes.replace('\n', '\r\n'))

    def test_rejects_previous_checkout_and_missing_release_version(self):
        for notes, field in (
            (self.notes.replace('--branch v2.3.4', '--branch v2.3.3'), 'git clone --branch'),
            (self.notes.splitlines()[0], 'TRIP_VERSION'),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, f'Release notes: {field}'):
                    verify_release_version(self.root, self.version, notes)

    def test_rejects_readme_with_previous_release_link(self):
        readme = self.readme.replace('/releases/tag/v2.3.4', '/releases/tag/v2.3.3')
        (self.root / 'README.md').write_text(readme, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'README.md: current Release link'):
            verify_release_version(self.root, self.version, self.notes)

    def test_rejects_one_application_image_with_previous_version(self):
        path = self.root / 'docker-compose.images.yml'
        config = path.read_text(encoding='utf-8')
        agent = 'ghcr.io/w205614/langchain-trip-planner-agent:'
        config = config.replace(agent + '${TRIP_VERSION:?Set TRIP_VERSION to the matching release tag}',
                                agent + 'v2.3.3')
        path.write_text(config, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Compose application images must be'):
            verify_release_version(self.root, self.version, self.notes)

    def test_rejects_implicit_fallback_to_previous_images(self):
        path = self.root / 'docker-compose.images.yml'
        config = path.read_text(encoding='utf-8').replace(
            '${TRIP_VERSION:?Set TRIP_VERSION to the matching release tag}', '${TRIP_VERSION:-v2.3.3}')
        path.write_text(config, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Compose must reject a missing TRIP_VERSION'):
            verify_release_version(self.root, self.version, self.notes)
