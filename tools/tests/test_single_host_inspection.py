import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import single_host_inspection as inspection


class SingleHostInspectionTests(unittest.TestCase):
    def test_real_filesystem_read_is_non_mutating_and_reports_file_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.vhdx'
            path.write_bytes(b'fixture')
            before = path.read_bytes()
            row = inspection.inspect_paths([path])[0]
            self.assertEqual(7, row['file_bytes'])
            self.assertGreater(row['total_bytes'], 0)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(Path(folder).iterdir()))

    def test_low_space_is_nonzero_and_default_does_not_call_docker(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            usage = shutil.disk_usage(folder)._replace(total=1000, free=50, used=950)
            with patch.object(inspection.shutil, 'disk_usage', return_value=usage), patch.object(inspection.subprocess, 'run', side_effect=AssertionError('No Docker mutation or probe requested')):
                self.assertEqual(1, inspection.main(['--path', folder, '--output', str(output)]))
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(report['capacity_within_warning'])
            self.assertIsNone(report['containers'])

    def test_missing_path_or_invalid_threshold_fails_instead_of_claiming_healthy(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError): inspection.inspect_paths([Path(folder)/'missing'])
            with self.assertRaises(ValueError): inspection.inspect_paths([folder], 0)
