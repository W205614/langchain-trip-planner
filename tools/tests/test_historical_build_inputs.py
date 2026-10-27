"""Faithful historical inputs: fail closed on tampering, extras and path escapes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import restore_historical_build_inputs as helper
from tools import risk_acceptance as risk


class HistoricalInputsTests(unittest.TestCase):
    def fixture(self, root):
        image_files, files = {}, []
        for name in sorted(helper.FILENAMES):
            raw = ('historical input: ' + name + '\r\n').encode()
            source = helper.EVIDENCE + '/evals-results/' + name
            target = helper.DESTINATION + '/' + name
            p = root / source; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
            image = '/app/evals/results/' + name
            image_files[image] = risk.application_digest(image, raw)
            files.append({'source': source, 'destination': target, 'image_path': image,
                          'bytes': len(raw), 'raw_sha256': risk.digest(raw),
                          'approved_application_sha256': image_files[image]})
        approval = root / '.github/security-risk-acceptance.json'; approval.parent.mkdir()
        approval.write_text(json.dumps({'approved_image': {'application_file_sha256': image_files}}))
        sha = risk.digest(approval.read_bytes())
        manifest = {'schema_version': 1, 'historical_only': True, 'fresh_evaluation': False,
                    'approved_business_revision': risk.APPROVED_REVISION, 'approved_image_id': risk.APPROVED_IMAGE,
                    'approval_sha256': sha, 'application_hash_algorithm': risk.APPLICATION_HASH_ALGORITHM, 'files': files}
        manifest_path = root / helper.EVIDENCE / 'manifest.json'; manifest_path.write_text(json.dumps(manifest))
        return manifest_path, manifest, sha

    def test_default_is_read_only_ci_copies_only_exact_missing_files_and_allows_identical_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, _, sha = self.fixture(root)
            with patch.object(helper, 'FROZEN_APPROVAL_SHA256', sha):
                result = helper.restore(root)
                self.assertEqual(12, result['files_verified']); self.assertFalse((root / helper.DESTINATION).exists())
                with patch.dict('os.environ', {'GITHUB_ACTIONS': 'false'}):
                    with self.assertRaises(RuntimeError): helper.restore(root, True)
                with patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                    result = helper.restore(root, True); self.assertEqual(12, result['files_restored'])
                    again = helper.restore(root, True); self.assertEqual(0, again['files_restored'])
                    self.assertEqual(12, again['existing_byte_identical']); self.assertFalse(again['fresh_evaluation'])

    def test_raw_tampering_manifest_rehash_and_existing_destination_difference_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest_path, manifest, sha = self.fixture(root); entry = manifest['files'][0]
            source = root / entry['source']; original = source.read_bytes()
            with patch.object(helper, 'FROZEN_APPROVAL_SHA256', sha):
                source.write_bytes(b'tampered')
                with self.assertRaises(RuntimeError): helper.restore(root)
                entry.update(bytes=8, raw_sha256=risk.digest(b'tampered'), approved_application_sha256=risk.digest(b'tampered'))
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaises(RuntimeError): helper.restore(root)
                source.write_bytes(original); entry.update(bytes=len(original), raw_sha256=risk.digest(original),
                    approved_application_sha256=risk.application_digest(entry['image_path'], original))
                manifest_path.write_text(json.dumps(manifest)); destination = root / entry['destination']
                destination.parent.mkdir(parents=True); destination.write_bytes(b'existing important mismatch')
                with patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                    with self.assertRaises(RuntimeError): helper.restore(root, True)
                self.assertEqual(b'existing important mismatch', destination.read_bytes())

    def test_extra_destination_file_and_source_or_target_path_traversal_block_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path, manifest, sha = self.fixture(root)
            destination = root / helper.DESTINATION; destination.mkdir(parents=True)
            extra = destination / 'unreviewed.json'; extra.write_bytes(b'must stay')
            with patch.object(helper, 'FROZEN_APPROVAL_SHA256', sha), patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}):
                with self.assertRaises(RuntimeError): helper.restore(root, True)
                self.assertEqual([extra], list(destination.iterdir())); self.assertEqual(b'must stay', extra.read_bytes())
                for key, value in [('source', '../outside.json'), ('destination', 'backend/evals/results/../../app/config.py')]:
                    altered = copy.deepcopy(manifest); altered['files'][0][key] = value; path.write_text(json.dumps(altered))
                    with self.assertRaises(RuntimeError): helper.restore(root, True)
                self.assertEqual([extra], list(destination.iterdir()))


if __name__ == '__main__':
    unittest.main()
