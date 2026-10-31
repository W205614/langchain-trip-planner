"""Bounded guards for CI fixture preparation; no application/browser execution."""
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from tools import ci_browser_contract_overlay as overlay


class BrowserFixturePreparationGuards(unittest.TestCase):
    def setUp(self):
        # Keep local verification artifacts; never recursively delete user files.
        self.root = overlay.ROOT / '.cache/ci-browser-guard-tests' / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.manifest = json.loads(overlay.MANIFEST.read_text(encoding='utf-8-sig'))
        for relative in overlay.REVIEWED_FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(overlay.ROOT / relative, target)

    def snapshot(self):
        return {relative: (self.root / relative).read_bytes() for relative in overlay.REVIEWED_FILES}

    def test_read_only_review_preserves_all_original_sources_and_test_contracts(self):
        original = self.snapshot()
        plan = overlay.reviewed_plan(self.root, self.manifest)
        self.assertEqual(len(plan), 6)
        self.assertEqual(original, self.snapshot())
        self.assertFalse((self.root / overlay.COPY_RELATIVE).exists())
        for item in plan.values():
            self.assertGreaterEqual(item['revised_expectations'], item['original_expectations'])

    def test_source_drift_unknown_paths_and_changed_anchors_fail_closed(self):
        relative = sorted(overlay.REVIEWED_FILES)[0]
        source = self.root / relative
        source.write_bytes(source.read_bytes() + b'\n// unexpected source revision\n')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            overlay.reviewed_plan(self.root, self.manifest)
        shutil.copyfile(overlay.ROOT / relative, source)
        changed = copy.deepcopy(self.manifest)
        changed['replacements'][0]['source_path'] = '../unreviewed.spec.ts'
        with self.assertRaisesRegex(RuntimeError, 'Exactly the six'):
            overlay.reviewed_plan(self.root, changed)
        changed = copy.deepcopy(self.manifest)
        changed['replacements'][0]['before'] = 'THIS_REVIEWED_ANCHOR_DOES_NOT_EXIST'
        with self.assertRaisesRegex(RuntimeError, 'exactly one occurrence'):
            overlay.reviewed_plan(self.root, changed)

    def test_additional_skips_or_removed_assertion_coverage_are_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed['replacements'][0]['after'] += "\ntest.skip('unreviewed skip', () => {})"
        with self.assertRaisesRegex(RuntimeError, 'declarations, timeouts or skip markers'):
            overlay.reviewed_plan(self.root, changed)
        changed = copy.deepcopy(self.manifest)
        # The reviewed legacy label replacement contains one existing expectation.
        label = next(item for item in changed['replacements'] if item['id'] == 'legacy-stale-status-and-warning')
        label['after'] = ''
        with self.assertRaisesRegex(RuntimeError, 'removed existing expectation'):
            overlay.reviewed_plan(self.root, changed)

    def test_explicit_ci_copy_keeps_originals_and_refuses_existing_targets(self):
        original = self.snapshot()
        plan = overlay.reviewed_plan(self.root, self.manifest)
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with self.assertRaisesRegex(RuntimeError, 'GITHUB_ACTIONS=true'):
                overlay.prepare(self.root, plan)
        self.assertFalse((self.root / overlay.COPY_RELATIVE).exists())
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            copied = overlay.prepare(self.root, plan)
            self.assertEqual(len(copied), 6)
            self.assertEqual(original, self.snapshot())
            with self.assertRaisesRegex(RuntimeError, 'existing copied suite'):
                overlay.prepare(self.root, plan)
        self.assertIn("...original", (self.root / overlay.CONFIG_RELATIVE).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
