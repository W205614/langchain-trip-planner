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
            os.link(binary / 'perl', binary / 'perl5.40.1')
            (binary / 'cp').write_bytes(b'keep-packaging-copy')
            (binary / 'sha256sum').write_bytes(b'keep-source-verification')
            result = tools.restrict(root, lambda path: None)
            self.assertEqual('verified_absent', result['status'])
            self.assertFalse((binary / 'renamed-hardlink').exists())
            self.assertFalse((binary / 'renamed-copy').exists())
            self.assertFalse((binary / 'perl5.40.1').exists())
            self.assertEqual(b'keep-packaging-copy', (binary / 'cp').read_bytes())
            self.assertEqual(b'keep-source-verification', (binary / 'sha256sum').read_bytes())
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

    def test_unused_tic_parser_and_aliases_removed_while_tinfo_ncurses_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.fixture(root)
            libraries = root / 'usr/lib/x86_64-linux-gnu'
            (libraries / 'libtic.so.6.5').write_bytes(b'unused-terminal-compiler-parser')
            if os.name == 'nt':
                # Windows host lacks symlink privilege; Linux CI exercises the
                # SONAME symlink, while this host exercises the hardlink alias.
                os.link(libraries / 'libtic.so.6.5', libraries / 'libtic.so.6')
            else:
                (libraries / 'libtic.so.6').symlink_to('libtic.so.6.5')
            shutil.copyfile(libraries / 'libtic.so.6.5', libraries / 'renamed-parser.so')
            (libraries / 'libtinfo.so.6').write_bytes(b'keep-terminal-runtime')
            (libraries / 'libncursesw.so.6').write_bytes(b'keep-curses-runtime')
            result = tools.restrict(root, lambda path: None)
            self.assertEqual('verified_absent', result['status'])
            self.assertFalse((root / 'usr/bin/toe').exists())
            for name in ('libtic.so.6', 'libtic.so.6.5', 'renamed-parser.so'):
                self.assertFalse((libraries / name).exists() or (libraries / name).is_symlink())
            self.assertEqual(b'keep-terminal-runtime', (libraries / 'libtinfo.so.6').read_bytes())
            self.assertEqual(b'keep-curses-runtime', (libraries / 'libncursesw.so.6').read_bytes())

    def test_dpkg_parent_traversal_is_rejected_before_any_target_is_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            (root / 'var/lib/dpkg/info/mount.list').write_text('/usr/bin/../../../outside/mount\n')
            with self.assertRaises(RuntimeError): tools.restrict(root, lambda path: None)
            self.assertTrue((binary / 'mount').exists())

    def test_dpkg_documentation_directory_is_preserved_but_bin_directory_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            documentation = root / 'usr/share/doc/mount'; documentation.mkdir(parents=True)
            (documentation / 'README').write_bytes(b'keep-package-documentation')
            (root / 'var/lib/dpkg/info/mount.list').write_text(
                '/usr/bin/mount\n/usr/bin/umount\n/usr/share/doc/mount\n')
            self.assertEqual('verified_absent', tools.restrict(root, lambda path: None)['status'])
            self.assertEqual(b'keep-package-documentation', (documentation / 'README').read_bytes())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = self.fixture(root)
            (binary / 'mount').unlink()
            (binary / 'mount').mkdir()
            (binary / 'mount/marker').write_bytes(b'preserve-abnormal-directory')
            with self.assertRaisesRegex(RuntimeError, 'Refusing to remove a directory'):
                tools.restrict(root, lambda path: None)
            self.assertEqual(b'preserve-abnormal-directory', (binary / 'mount/marker').read_bytes())
            self.assertTrue((binary / 'nsenter').exists())


if __name__ == '__main__':
    unittest.main()
