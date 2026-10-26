"""Real OS process-lock counterexamples, with every Docker operation forbidden."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from unittest.mock import Mock
from uuid import uuid4

import pytest

SCRIPTS=Path(__file__).parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import automated_maintenance as maintenance


CHILD="""
import sys
sys.path.insert(0,sys.argv[1])
import automated_maintenance as maintenance
with maintenance.maintenance_lock(sys.argv[2]):
    print('lock-acquired',flush=True)
    sys.stdin.readline()
"""


def holder(tmp_path,monkeypatch):
    monkeypatch.setenv("TRIP_MAINTENANCE_LOCK_DIR",str(tmp_path/"shared-locks"))
    project="lock-counterexample-"+uuid4().hex
    process=subprocess.Popen([sys.executable,"-c",CHILD,str(SCRIPTS),project],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ))
    ready=queue.Queue()
    threading.Thread(target=lambda:ready.put(process.stdout.readline()),daemon=True).start()
    try:
        assert ready.get(timeout=15).strip()=="lock-acquired"
    except Exception:
        process.terminate();process.wait(timeout=10);raise
    return project,process


def release(process):
    if process.poll() is None:
        process.stdin.write("release\n");process.stdin.flush()
    process.wait(timeout=10)
    process.stdin.close();process.stdout.close();process.stderr.close()


@pytest.mark.parametrize("operation",["backup","recover","inspection"])
def test_another_process_is_rejected_before_snapshot_or_any_docker_mutation(tmp_path,monkeypatch,operation):
    project,process=holder(tmp_path,monkeypatch)
    forbidden=Mock(side_effect=AssertionError("Busy maintenance must not inspect or mutate Docker"))
    monkeypatch.setattr(maintenance,"snapshot",forbidden)
    monkeypatch.setattr(maintenance,"docker",forbidden)
    try:
        with pytest.raises(maintenance.MaintenanceBusy):
            if operation=="recover":maintenance.recover_scheduler(project,execute=True)
            else:maintenance.run_backup(project,tmp_path/"backups",execute=operation=="backup")
        forbidden.assert_not_called()
    finally:release(process)
    assert list((tmp_path/"shared-locks").glob("*.lock")),"Lock files persist after release"
    with maintenance.maintenance_lock(project):pass


def test_os_releases_lock_when_holder_process_terminates_without_cleanup(tmp_path,monkeypatch):
    project,process=holder(tmp_path,monkeypatch)
    process.terminate();process.wait(timeout=10)
    process.stdin.close();process.stdout.close();process.stderr.close()
    files=list((tmp_path/"shared-locks").glob("*.lock"))
    assert len(files)==1
    with maintenance.maintenance_lock(project) as locked:
        assert locked==files[0]
    assert files[0].is_file()


def test_different_project_has_an_independent_lock(tmp_path,monkeypatch):
    project,process=holder(tmp_path,monkeypatch)
    try:
        with maintenance.maintenance_lock(project+"-different"):
            assert len(list((tmp_path/"shared-locks").glob("*.lock")))==2
    finally:release(process)
