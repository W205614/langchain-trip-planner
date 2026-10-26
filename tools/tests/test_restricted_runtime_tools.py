"""CLI-only image cleanup must be precise and read-only verification fail closed."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('restricted_tools', Path(__file__).resolve().parents[2] / 'backend/scripts/restrict_runtime_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class RestrictedToolsTests(unittest.TestCase):
    def fixture(self, root):
        binary = root / 'usr/bin'; binary.mkdir(parents=True)
        for name in tools.TOOLS:
            (binary / name).write_bytes(('fixture-executable-' + name).encode())
        (binary / 'python3.11').write_bytes(b'keep-python')
        libraries = root / 'usr/lib/x86_64-linux-gnu'; libraries.mkdir(parents=True)
        (libraries / 'libc.so.6').write_bytes(b'keep-libc')
        (libraries / 'libstdc++.so.6').write_bytes(b'keep-libstdcpp')
        package = root / 'var/lib/dpkg'; (package / 'info').mkdir(parents=True)
        (package / 'status').write_bytes(b'keep-essential-package-database')
        (package / 'info/mount.list').write_text('/usr/bin/mount\n/usr/bin/umount\n')
        return binary

    def test_exact_hardlinks_and_copies_removed_while_python_libraries_and_packages_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            os.link(binary / 'nsenter', binary / 'renamed-hardlink')
            shutil.copyfile(binary / 'unshare', binary / 'renamed-copy')
            result = tools.restrict(root, lambda path: None)
            self.assertEqual('verified_absent', result['status'])
            self.assertFalse((binary / 'renamed-hardlink').exists())
            self.assertFalse((binary / 'renamed-copy').exists())
            self.assertEqual(b'keep-python', (binary / 'python3.11').read_bytes())
            self.assertEqual(b'keep-libc', (root / 'usr/lib/x86_64-linux-gnu/libc.so.6').read_bytes())
            self.assertEqual(b'keep-essential-package-database', (root / 'var/lib/dpkg/status').read_bytes())

    def test_check_finds_reintroduced_copy_with_file_capability_without_removing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            copied = (binary / 'mount').read_bytes()
            manifest = tools.restrict(root, lambda path: None)
            (binary / 'unexpected-helper').write_bytes(copied)
            rows = tools.scan(root, manifest['fingerprints'], lambda path: 'fixture-capability')
            self.assertEqual(1, len(rows))
            self.assertEqual('restricted_binary_identity', rows[0]['reason'])
            self.assertEqual('fixture-capability', rows[0]['file_capability'])
            self.assertTrue((binary / 'unexpected-helper').exists())

    def test_dpkg_parent_traversal_is_rejected_before_any_target_is_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            (root / 'var/lib/dpkg/info/mount.list').write_text('/usr/bin/../../../outside/mount\n')
            with self.assertRaises(RuntimeError): tools.restrict(root, lambda path: None)
            self.assertTrue((binary / 'mount').exists())


if __name__ == '__main__':
    unittest.main()
