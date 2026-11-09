import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
