"""Silent Windows task entry; reuse frozen maintenance logic and preserve failures.

pythonw suppresses only its own console. Every Popen descendant launched by the
maintenance Python process also needs CREATE_NO_WINDOW/hidden startup policy.
Captured/binary pipes and explicit streams must keep their original semantics.
Logs are private backup-root/task-logs files, not public delivery evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import copy
from datetime import datetime, timezone
import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def silent_options(options, log):
    """Add native Windows policy without replacing input/capture/binary pipes."""
    result = dict(options)
    if os.name != 'nt':
        raise RuntimeError('The silent backup task entry is Windows-only')
    flags = result.get('creationflags', 0)
    if flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS):
        raise ValueError('Visible/detached console flags conflict with silent backup policy')
    result['creationflags'] = flags | subprocess.CREATE_NO_WINDOW
    startup = copy.copy(result.get('startupinfo')) if result.get('startupinfo') else subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result['startupinfo'] = startup
    if result.get('stdin') is None:
        result['stdin'] = subprocess.DEVNULL
    if result.get('stdout') is None:
        result['stdout'] = log
    if result.get('stderr') is None:
        result['stderr'] = log
    return result


@contextmanager
def silent_processes(log):
    original = subprocess.Popen
    signature = inspect.signature(original)
    audit = {'runner_pid': os.getpid(), 'child_creation_attempts': 0,
             'child_configuration_count': 0, 'children': []}

    def quiet_popen(*args, **kwargs):
        audit['child_creation_attempts'] += 1
        bound = signature.bind_partial(*args, **kwargs)
        bound.arguments.update(silent_options(bound.arguments, log))
        audit['child_configuration_count'] += 1
        command = bound.arguments.get('args')
        executable = bound.arguments.get('executable')
        if executable is None and isinstance(command, (list, tuple)) and command:
            executable = command[0]
        basename = Path(str(executable)).name if executable is not None else 'string_command_not_recorded'
        startup = bound.arguments['startupinfo']
        child = original(*bound.args, **bound.kwargs)
        record = {'event': 'child_started', 'pid': child.pid, 'executable_basename': basename,
                  'creationflags': bound.arguments['creationflags'],
                  'startup_dwFlags': startup.dwFlags, 'startup_wShowWindow': startup.wShowWindow,
                  'startupinfo_stage': 'policy passed to Popen before native standard-handle augmentation'}
        audit['children'].append(record)
        log.write(json.dumps(record) + '\n'); log.flush()
        original_communicate, original_wait = child.communicate, child.wait
        noted = False

        def note_exit():
            nonlocal noted
            record['exit_code'] = child.returncode
            if child.returncode and not noted:
                log.write(json.dumps({'event': 'child_failed', 'returncode': child.returncode}) + '\n')
                log.flush(); noted = True

        def wait(*wait_args, **wait_kwargs):
            code = original_wait(*wait_args, **wait_kwargs)
            note_exit()
            return code

        def communicate(*comm_args, **comm_kwargs):
            output, error = original_communicate(*comm_args, **comm_kwargs)
            note_exit()
            # Captured stdout may be a database dump or private SQL data: never
            # duplicate it. Preserve nonzero child stderr for private diagnosis.
            if child.returncode and error:
                text = error.decode('utf-8', errors='backslashreplace') if isinstance(error, bytes) else error
                log.write('captured_child_stderr:\n' + text + '\n'); log.flush()
            return output, error

        child.wait, child.communicate = wait, communicate
        return child

    subprocess.Popen = quiet_popen
    try:
        yield audit
    finally:
        subprocess.Popen = original


def load_maintenance():
    scripts = ROOT / 'backend/scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location('trip_silent_scheduled_maintenance', scripts / 'automated_maintenance.py')
        maintenance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(maintenance)
        # Recovery is an external host wrapper; frozen application/image bytes
        # stay unchanged. Missing recovery code fails before any backup starts.
        try:
            from tools.windows_backup_recovery import execute_backup
        except ModuleNotFoundError:
            from windows_backup_recovery import execute_backup
        maintenance.run_backup = lambda project, root, replica_root=None, execute=False: execute_backup(
            maintenance, project, root, replica_root, execute)
        return maintenance
    finally:
        sys.path.pop(0)


def run_main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--backup-root', type=Path, default=ROOT / 'backups')
    options, _ = preliminary.parse_known_args(argv)
    log_dir = options.backup_root.resolve() / 'task-logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + str(os.getpid()) + '-' + uuid4().hex[:8]
    log_path = log_dir / ('silent-backup-' + stamp + '.log')
    report_path = log_dir / ('silent-backup-' + stamp + '.json')
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'log': str(log_path),
              'entry': str(Path(__file__).resolve()), 'runner_pid': os.getpid(), 'exit_code': 1,
              'boundary': 'Private inherited-user log ACLs; Windows mode0600 alone is not a POSIX ACL guarantee.'}
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            log.write(json.dumps({'event': 'runner_started', 'pid': os.getpid()}) + '\n'); log.flush()
            try:
                with silent_processes(log) as audit:
                    report['process_audit'] = audit
                    result = load_maintenance().main(argv)
                    report['exit_code'] = int(result or 0)
            except SystemExit as error:
                report['exit_code'] = error.code if isinstance(error.code, int) else 1
                if error.code is None:
                    report['exit_code'] = 0
                if error.code and not isinstance(error.code, int):
                    print(str(error.code), file=log)
            except BaseException as error:
                report['error_type'] = type(error).__name__
                traceback.print_exc(file=log)
                report['exit_code'] = 1
            finally:
                report['child_count'] = len(report.get('process_audit', {}).get('children', []))
                report['child_configuration_count'] = report.get('process_audit', {}).get('child_configuration_count', 0)
                report['finished_at'] = datetime.now(timezone.utc).isoformat()
                log.write(json.dumps({'event': 'runner_finished', 'exit_code': report['exit_code']}) + '\n')
    fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2); stream.write('\n')
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(run_main())
