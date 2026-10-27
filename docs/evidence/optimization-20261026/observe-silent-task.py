"""One explicitly authorized scheduler run plus passive owned-window samples."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
BACKUPS = ROOT / 'backups/automated-daily'
REPORT = Path(__file__).resolve().parent / 'windows-silent-task-actual-result.json'


def powershell(code):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup)
    return result.stdout.decode('utf-8-sig').strip()


def visible_owned_windows(pids):
    observed = []
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    @callback_type
    def callback(handle, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(handle):
            kind = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(handle, kind, len(kind))
            observed.append({'pid': pid.value, 'window_class': kind.value})
        return True
    user.EnumWindows(callback, 0)
    return observed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-task', action='store_true')
    args = parser.parse_args()
    if not args.run_task:
        print(json.dumps({'run_performed': False, 'scope': 'Requires explicit --run-task; starts only TripPlanner-AutomatedBackup once.'}))
        return 0
    assert os.name == 'nt' and not REPORT.exists()
    state = powershell("(Get-ScheduledTask -TaskName 'TripPlanner-AutomatedBackup').State.ToString()")
    assert state == 'Ready'
    logs = BACKUPS / 'task-logs'
    before = set(logs.glob('silent-backup-*.log')) if logs.exists() else set()
    began = time.monotonic()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'task_started_once': True, 'window_samples': 0, 'owned_visible_windows': [],
              'boundary': 'Passive EnumWindows sampling of this runner/recorded native child PIDs. Native process flags and a separate actual child GetConsoleWindow=0 test complement sampling; this does not inspect every window on the host.'}
    powershell("Start-ScheduledTask -TaskName 'TripPlanner-AutomatedBackup'")
    last_state_check = 0
    deadline = began + 600
    task_state = 'Running'
    observed_pids = set()
    while time.monotonic() < deadline:
        new = set(logs.glob('silent-backup-*.log')) - before if logs.exists() else set()
        for path in new:
            try:
                observed_pids.add(int(path.stem.split('-')[-2]))
                for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                    if line.startswith('{"event": "child_started"'):
                        observed_pids.add(json.loads(line)['pid'])
            except (ValueError, OSError):
                pass
        report['owned_visible_windows'].extend(visible_owned_windows(observed_pids))
        report['window_samples'] += 1
        if time.monotonic() - last_state_check >= 8:
            task_state = powershell("(Get-ScheduledTask -TaskName 'TripPlanner-AutomatedBackup').State.ToString()")
            last_state_check = time.monotonic()
            if task_state != 'Running' and new:
                break
        time.sleep(0.25)
    report['task_state_after'] = task_state
    report['observed_pid_count'] = len(observed_pids)
    info = json.loads(powershell("Get-ScheduledTaskInfo -TaskName 'TripPlanner-AutomatedBackup' | Select-Object LastTaskResult,@{n='LastRunTime';e={$_.LastRunTime.ToString('o')}},@{n='NextRunTime';e={$_.NextRunTime.ToString('o')}} | ConvertTo-Json -Compress"))
    report['scheduler'] = info
    new_logs = sorted(set(logs.glob('silent-backup-*.log')) - before)
    assert len(new_logs) == 1, 'A single task invocation must produce exactly one new runner log'
    runner_path = new_logs[0].with_suffix('.json')
    runner = json.loads(runner_path.read_text(encoding='utf-8'))
    audit = runner['process_audit']
    children = audit['children']
    report['native_child_configuration'] = {
        'children': len(children), 'attempts': audit['child_creation_attempts'],
        'configured': audit['child_configuration_count'],
        'all_create_no_window': all(x['creationflags'] & subprocess.CREATE_NO_WINDOW for x in children),
        'all_startup_hide': all(x['startup_dwFlags'] & subprocess.STARTF_USESHOWWINDOW and x['startup_wShowWindow'] == subprocess.SW_HIDE for x in children),
        'nonzero_child_exits': [x['exit_code'] for x in children if x.get('exit_code')],
    }
    backup = json.loads((BACKUPS / 'last-run.json').read_text(encoding='utf-8'))
    report['backup'] = {key: backup.get(key) for key in ('passed', 'elapsed_seconds', 'original_running_state_restored', 'ready_after_restore', 'errors')}
    report['runner_exit_code'] = runner['exit_code']
    report['private_log'] = str(new_logs[0])
    report['private_runner_report_sha256'] = hashlib.sha256(runner_path.read_bytes()).hexdigest()
    report['private_log_sha256'] = hashlib.sha256(new_logs[0].read_bytes()).hexdigest()
    report['passed'] = (info['LastTaskResult'] == 0 and runner['exit_code'] == 0 and backup.get('passed') is True
        and backup.get('original_running_state_restored') is True and not report['owned_visible_windows']
        and bool(children) and report['native_child_configuration']['all_create_no_window']
        and report['native_child_configuration']['all_startup_hide'])
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['total_observation_seconds'] = round(time.monotonic() - began, 2)
    REPORT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'task_exit': info['LastTaskResult'],
        'backup_seconds': backup.get('elapsed_seconds'), 'children': len(children),
        'window_samples': report['window_samples'], 'visible_owned_windows': len(report['owned_visible_windows'])}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
