"""Recovery must await healthy containers AND the public validation entry."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from urllib.error import URLError

import pytest

spec = importlib.util.spec_from_file_location("recovery_readiness", Path(__file__).resolve().parents[1] / "scripts/java_recovery_drill.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_service_health_waits_for_both_containers(monkeypatch):
    states = iter([("running", "healthy"), ("running", "starting"),
                   ("running", "healthy"), ("running", "healthy")])
    sleeps = []
    def inspect(_):
        state, health = next(states)
        return json.dumps({"Status": state, "Health": {"Status": health}}).encode()
    monkeypatch.setattr(recovery.subprocess, "check_output", inspect)
    monkeypatch.setattr(recovery.time, "sleep", sleeps.append)
    recovery.wait_for_service_health(lambda *args: args[-1].encode())
    assert sleeps == [.5]


def test_service_timeout_does_not_accept_running_without_health(monkeypatch):
    times = iter([0, 0, 91])
    monkeypatch.setattr(recovery.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    monkeypatch.setattr(recovery.subprocess, "check_output", lambda _: b'{"Status":"running"}')
    with pytest.raises(RuntimeError, match="not healthy"):
        recovery.wait_for_service_health(lambda *args: b"fixture")


def test_public_entry_retries_startup_failure(monkeypatch):
    attempts = []
    def fetch(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise URLError("502 while starting")
        return io.BytesIO(b'{"offline_fixture":true}')
    monkeypatch.setattr(recovery, "urlopen", fetch)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    recovery.wait_for_public_fixture()
    assert len(attempts) == 2


def test_public_entry_rejects_non_fixture(monkeypatch):
    monkeypatch.setattr(recovery, "urlopen", lambda *a, **kw: io.BytesIO(b'{"offline_fixture":false}'))
    with pytest.raises(RuntimeError, match="not the isolated"):
        recovery.wait_for_public_fixture()


def test_command_failure_exposes_stderr_not_backup_stdout(monkeypatch, capsys):
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(a[0], 1, stdout=b"private-backup-bytes", stderr=b"fixture command failed"))
    with pytest.raises(subprocess.CalledProcessError):
        recovery.compose_command("start", "backend", "agent")
    captured = capsys.readouterr()
    assert "fixture command failed" in captured.err
    assert "private-backup-bytes" not in captured.out + captured.err
