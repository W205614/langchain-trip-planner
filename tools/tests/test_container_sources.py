import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.container_sources import Collector, paragraphs


class SourceCollectionTests(unittest.TestCase):
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
