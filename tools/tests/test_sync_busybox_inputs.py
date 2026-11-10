import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import sync_busybox_inputs


class BusyBoxInputSyncTests(unittest.TestCase):
    def test_text_normalization_does_not_change_compressed_recipe_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'deploy/images/busybox-backport'
            source.mkdir(parents=True)
            for name in sync_busybox_inputs.TEXT_FILES:
                (source / name).write_bytes(b'first\r\nsecond\r\n')
            compressed = b'\x1f\x8b\x08\x00binary\r\n\x00\x80payload'
            (source / 'aports-busybox.tar.gz').write_bytes(compressed)
            with patch.object(sync_busybox_inputs, 'ROOT', root):
                sync_busybox_inputs.sync()
                sync_busybox_inputs.sync(check=True)
            for service in ('frontend', 'business-backend'):
                target = root / service / 'security/busybox-backport'
                self.assertEqual((target / 'aports-busybox.tar.gz').read_bytes(), compressed)
                self.assertEqual((target / 'build.sh').read_bytes(), b'first\nsecond\n')

    def test_check_rejects_a_binary_copy_changed_only_by_newline_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'deploy/images/busybox-backport'
            source.mkdir(parents=True)
            for name in sync_busybox_inputs.FILES:
                (source / name).write_bytes(b'payload\r\n')
            with patch.object(sync_busybox_inputs, 'ROOT', root):
                sync_busybox_inputs.sync()
                (root / 'frontend/security/busybox-backport/aports-busybox.tar.gz').write_bytes(b'payload\n')
                with self.assertRaisesRegex(SystemExit, 'Generated BusyBox input differs'):
                    sync_busybox_inputs.sync(check=True)

    def test_real_vendored_recipe_matches_the_existing_lock_in_both_contexts(self):
        root = Path(__file__).resolve().parents[2]
        canonical = root / 'deploy/images/busybox-backport'
        lock = dict(line.split('=', 1) for line in (canonical / 'source.lock').read_text().splitlines())
        original = (canonical / 'aports-busybox.tar.gz').read_bytes()
        self.assertEqual(len(original), 75751)
        self.assertEqual(hashlib.sha256(original).hexdigest(), lock['APKBUILD_SHA256'])
        for service in ('frontend', 'business-backend'):
            self.assertEqual((root / service / 'security/busybox-backport/aports-busybox.tar.gz').read_bytes(), original)
