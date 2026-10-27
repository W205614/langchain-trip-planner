"""Meaningful fixture-overlay guards without modifying the repository backend."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import ci_contract_fixture_overlay as overlay


class OverlayTests(unittest.TestCase):
    def copy_inputs(self, root):
        for name in [overlay.MANIFEST, *overlay.BEFORE_HASHES]:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(overlay.ROOT / name, destination)

    def test_default_read_only_explicit_ci_and_assertions_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.copy_inputs(root)
            original = {name: (root / name).read_bytes() for name in overlay.BEFORE_HASHES}
            self.assertEqual(0, overlay.apply_overlay(root)['files_written'])
            self.assertEqual(original, {name: (root / name).read_bytes() for name in original})
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), self.assertRaises(RuntimeError):
                overlay.apply_overlay(root, True)
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
                result = overlay.apply_overlay(root, True)
                self.assertEqual(2, result['files_written'])
                self.assertEqual(0, overlay.apply_overlay(root, True)['files_written'])
            for name, expected in overlay.AFTER_HASHES.items():
                self.assertEqual(expected, overlay.digest((root / name).read_bytes().replace(b'\r\n', b'\n')))

    def test_changed_second_file_aborts_before_any_first_file_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.copy_inputs(root)
            first, second = list(overlay.BEFORE_HASHES)
            original_first = (root / first).read_bytes()
            with (root / second).open('ab') as stream:
                stream.write(b'\n# unreviewed source drift\n')
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), self.assertRaisesRegex(RuntimeError, 'source drift'):
                overlay.apply_overlay(root, True)
            self.assertEqual(original_first, (root / first).read_bytes())

    def test_manifest_assertion_or_destination_changes_are_not_rebaselined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.copy_inputs(root)
            path = root / overlay.MANIFEST
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['files'][0]['path'] = '../outside.py'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), self.assertRaisesRegex(RuntimeError, 'manifest changed'):
                overlay.apply_overlay(root, True)


if __name__ == '__main__':
    unittest.main()
