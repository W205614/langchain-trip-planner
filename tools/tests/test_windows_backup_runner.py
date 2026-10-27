"""Host-only native process/pipe checks; no Docker or scheduled task execution."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

PATH = Path(__file__).resolve().parents[1] / 'windows_backup_runner.pyw'
loader = importlib.machinery.SourceFileLoader('tested_quiet_backup_runner', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
runner = importlib.util.module_from_spec(spec); loader.exec_module(runner)


@unittest.skipUnless(os.name == 'nt', 'Native Windows console policy')
class SilentRunnerTests(unittest.TestCase):
    def test_policy_rejects_visible_detached_console_and_preserves_explicit_pipes(self):
        log = io.StringIO()
        for flags in (subprocess.CREATE_NEW_CONSOLE, subprocess.DETACHED_PROCESS):
            with self.assertRaises(ValueError): runner.silent_options({'creationflags': flags}, log)
        options = runner.silent_options({'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE,
                                         'stderr': subprocess.STDOUT, 'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}, log)
        self.assertEqual(subprocess.PIPE, options['stdin']); self.assertEqual(subprocess.PIPE, options['stdout'])
        self.assertEqual(subprocess.STDOUT, options['stderr'])
        self.assertEqual(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP, options['creationflags'])
        self.assertTrue(options['startupinfo'].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(subprocess.SW_HIDE, options['startupinfo'].wShowWindow)
        self.assertEqual(subprocess.DEVNULL, runner.silent_options({'stdin': None}, log)['stdin'])
        stream = io.BytesIO(b'input')
        self.assertIs(stream, runner.silent_options({'stdin': stream}, log)['stdin'])

    def test_actual_child_input_capture_and_native_hidden_flags_without_docker(self):
        code = '''import ctypes,json,sys
from ctypes import wintypes as w
class S(ctypes.Structure):
 _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR),('x',w.DWORD),('y',w.DWORD),('cx',w.DWORD),('cy',w.DWORD),('charsx',w.DWORD),('charsy',w.DWORD),('fill',w.DWORD),('flags',w.DWORD),('show',w.WORD),('reserved2count',w.WORD),('reserved2',ctypes.c_void_p),('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]
s=S();s.cb=ctypes.sizeof(s);ctypes.windll.kernel32.GetStartupInfoW(ctypes.byref(s))
print(json.dumps({'input':sys.stdin.buffer.read().hex(),'window':ctypes.windll.kernel32.GetConsoleWindow(),'flags':s.flags,'show':s.show}))
print('captured stderr',file=sys.stderr)
'''
        with tempfile.TemporaryDirectory() as tmp:
            with (Path(tmp) / 'private.log').open('w+', encoding='utf-8') as log:
                real_create = subprocess._winapi.CreateProcess
                observed = []
                def create(*args):
                    observed.append((args[5], args[8].dwFlags, args[8].wShowWindow))
                    return real_create(*args)
                with runner.silent_processes(log) as audit, patch.object(subprocess._winapi, 'CreateProcess', side_effect=create):
                    result = subprocess.run([sys.executable, '-c', code], input=b'\x00binary\xff', capture_output=True, check=True)
                data = json.loads(result.stdout)
                self.assertEqual(b'\x00binary\xff'.hex(), data['input']); self.assertEqual(0, data['window'])
                # venv's launcher may spawn the base interpreter with its own
                # startupinfo. Verify the actual parent WinAPI boundary instead.
                self.assertEqual(1, len(observed))
                self.assertTrue(observed[0][0] & subprocess.CREATE_NO_WINDOW)
                self.assertTrue(observed[0][1] & subprocess.STARTF_USESHOWWINDOW)
                self.assertEqual(0, observed[0][2])
                self.assertEqual(1, audit['child_configuration_count'])
                self.assertEqual(1, len(audit['children']))
                self.assertEqual(result.returncode, audit['children'][0]['exit_code'])
                self.assertNotIn('args', audit['children'][0])
                self.assertIn(b'captured stderr', result.stderr)

    def test_default_valid_handles_and_nonzero_captured_stderr_are_logged_exitcode_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'private.log'
            with path.open('w+', encoding='utf-8') as log:
                with runner.silent_processes(log):
                    result = subprocess.run([sys.executable, '-c', "import sys; print('stdout-log'); print('stderr-log',file=sys.stderr); assert sys.stdin.read()=='';sys.exit(7)"])
                    captured = subprocess.run([sys.executable, '-c', "import sys;assert sys.stdin.read()=='';print('diagnostic-error',file=sys.stderr);sys.exit(9)"], stdin=None, capture_output=True)
                self.assertEqual(7, result.returncode); self.assertEqual(9, captured.returncode)
            text = path.read_text(encoding='utf-8')
            for expected in ('stdout-log', 'stderr-log', 'diagnostic-error', '"returncode": 7', '"returncode": 9'):
                self.assertIn(expected, text)

    def test_pythonw_none_streams_get_private_report_and_maintenance_return_or_exception_survives(self):
        proof = {'passed': True, 'allowed_principals': 3, 'before_log_creation': True}
        with patch.object(runner, 'ensure_private_backup_root', return_value=proof):
            root = runner.ROOT / '.cache/windows-backup-main-guards' / uuid4().hex
            root.mkdir(parents=True)
            original = subprocess.Popen
            def fake_main(argv):
                print('{"passed":false,"synthetic_only":true}'); return 11
            with patch.object(runner, 'load_maintenance', return_value=SimpleNamespace(main=fake_main)), \
                 patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
                result = runner.run_main(['backup', '--backup-root', str(root)])
            self.assertEqual(11, result); self.assertIs(original, subprocess.Popen)
            first = list((root / 'task-logs').glob('*.json'))
            self.assertEqual(11, json.loads(first[0].read_text())['exit_code'])
            with patch.object(runner, 'load_maintenance', side_effect=RuntimeError('host synthetic failure')):
                self.assertEqual(1, runner.run_main(['backup', '--backup-root', str(root)]))
            logs = '\n'.join(p.read_text(encoding='utf-8') for p in (root / 'task-logs').glob('*.log'))
            self.assertIn('host synthetic failure', logs); self.assertIn('synthetic_only', logs)


class PrivatePrelogGuards(unittest.TestCase):
    def test_acl_failure_precedes_log_creation_and_maintenance(self):
        root = runner.ROOT / '.cache/windows-private-prelog-guards' / uuid4().hex
        with patch.object(runner, 'ensure_private_backup_root', side_effect=RuntimeError('synthetic denied ACL')), \
             patch.object(runner, 'load_maintenance') as maintenance:
            self.assertEqual(1, runner.run_main(['backup', '--backup-root', str(root)]))
        maintenance.assert_not_called()
        self.assertFalse(root.exists())

    def test_bootstrap_is_hidden_before_monkeypatch_and_rejects_invalid_proof(self):
        root = runner.ROOT / 'backups'
        constants = {'CREATE_NEW_CONSOLE': 16, 'DETACHED_PROCESS': 8, 'CREATE_NO_WINDOW': 0x08000000,
                     'STARTF_USESHOWWINDOW': 1, 'SW_HIDE': 0,
                     'STARTUPINFO': lambda: SimpleNamespace(dwFlags=0, wShowWindow=0)}
        proof = {'passed': True, 'allowed_principals': 3, 'before_log_creation': True}
        with patch.object(runner.os, 'name', 'nt'), patch.object(runner.os.path, 'isfile', return_value=True), \
             patch.multiple(runner.subprocess, create=True, **constants), \
             patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(proof))) as native:
            self.assertEqual(proof, runner.ensure_private_backup_root(root))
            options = native.call_args.kwargs
            self.assertTrue(options['creationflags'] & 0x08000000)
            self.assertTrue(options['startupinfo'].dwFlags & 1)
            self.assertEqual(0, options['startupinfo'].wShowWindow)
            self.assertEqual(subprocess.DEVNULL, options['stdin'])
            self.assertEqual(subprocess.PIPE, options['stdout']); self.assertEqual(subprocess.PIPE, options['stderr'])
            self.assertEqual(os.path.join(os.path.dirname(native.call_args.args[0][0]), 'Modules'), options['env']['PSModulePath'])
            command = native.call_args.args[0]
            self.assertEqual('EnsureBackupRoot', command[command.index('-Mode') + 1])
            self.assertEqual('Hidden', command[command.index('-WindowStyle') + 1])
            for response in (SimpleNamespace(returncode=1, stdout=''),
                             SimpleNamespace(returncode=0, stdout='not-json'),
                             SimpleNamespace(returncode=0, stdout='{"passed":false}')):
                native.return_value = response
                with self.assertRaises(RuntimeError): runner.ensure_private_backup_root(root)


if __name__ == '__main__':
    unittest.main()
