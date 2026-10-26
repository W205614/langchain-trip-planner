"""Failure-injection tests for exact project/state restoration; no live Docker."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import automated_maintenance as maintenance


def environment(monkeypatch, states=None):
    current = {name: True for name in ("backend", "agent", "postgres", "frontend")}
    current.update(states or {})
    calls = []
    def services(_project):
        return {name: {"Id": name + "-selected", "Image": "isolated-image", "State": {"Running": running},
            "Config": {"Env": ["POSTGRES_USER=trip", "POSTGRES_DB=isolated"]},
            "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18380"}]}}}
            for name, running in current.items()}
    def docker(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": args[2]}]).encode()
        if args[0] in ("stop", "start", "restart"):
            name = args[-1].removesuffix("-selected")
            current[name] = args[0] != "stop"
        return b""
    monkeypatch.setattr(maintenance, "services", services)
    monkeypatch.setattr(maintenance, "docker", docker)
    monkeypatch.setattr(maintenance, "backup", Mock())
    monkeypatch.setattr(maintenance, "validate_manifest", Mock(return_value={}))
    monkeypatch.setattr(maintenance, "_wait_ready", Mock())
    monkeypatch.setattr(maintenance, "sql", Mock(return_value="f"))
    return current, calls, docker


def test_first_restore_failure_cannot_prevent_restoring_other_service(tmp_path, monkeypatch):
    current, calls, original = environment(monkeypatch)
    def docker(*args, **kwargs):
        if args[:2] == ("start", "backend-selected"):
            calls.append(args)
            raise RuntimeError("private path or credential detail must not be reported")
        return original(*args, **kwargs)
    monkeypatch.setattr(maintenance, "docker", docker)
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("isolated", tmp_path, execute=True)
    report = failed.value.report
    assert ("start", "agent-selected") in calls
    assert current["agent"] is True and current["backend"] is False
    assert report["local_verified"] is True and report["passed"] is False
    assert any(error["service"] == "backend" and error["step"] == "restore_start" for error in report["errors"])
    assert not (tmp_path / "last-success.json").exists()
    assert "credential detail" not in json.dumps(report)


def test_backup_failure_restores_both_original_running_services_and_waits_for_readiness(tmp_path, monkeypatch):
    current, calls, _ = environment(monkeypatch)
    maintenance.backup.side_effect = RuntimeError("archive failure")
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("isolated", tmp_path, execute=True)
    assert current == {name: True for name in current}
    assert ("start", "backend-selected") in calls and ("start", "agent-selected") in calls
    assert maintenance._wait_ready.call_args_list[0].args == ("backend-selected", "backend")
    assert maintenance._wait_ready.call_args_list[1].args == ("agent-selected", "agent")
    assert failed.value.report["original_running_state_restored"] is True
    assert failed.value.report["passed"] is False


def test_unready_container_is_not_reported_as_success_even_when_running(tmp_path, monkeypatch):
    current, _, _ = environment(monkeypatch)
    maintenance._wait_ready.side_effect = [RuntimeError("not ready"), None]
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("isolated", tmp_path, execute=True)
    assert current["backend"] and current["agent"]
    assert failed.value.report["original_running_state_restored"] is True
    assert failed.value.report["ready_after_restore"] == {"backend": False, "agent": True}
    assert failed.value.report["passed"] is False
    assert not (tmp_path / "last-success.json").exists()


def test_multiple_restore_errors_are_all_recorded_and_stopped_postgres_is_attempted(tmp_path, monkeypatch):
    _, calls, original = environment(monkeypatch, {"postgres": False})
    def docker(*args, **kwargs):
        if args in (("start", "backend-selected"), ("stop", "postgres-selected")):
            calls.append(args)
            raise RuntimeError("injected restore failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(maintenance, "docker", docker)
    maintenance._wait_ready.side_effect = RuntimeError("agent readiness failure")
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("isolated", tmp_path, execute=True)
    assert ("start", "agent-selected") in calls
    assert ("stop", "postgres-selected") in calls
    errors = failed.value.report["errors"]
    assert {(error["step"], error["service"]) for error in errors} >= {
        ("restore_start", "backend"), ("restore_readiness", "agent"), ("restore_original_stopped_state", "postgres")}


def test_originally_stopped_services_are_preserved_on_success(tmp_path, monkeypatch):
    states = {name: False for name in ("backend", "agent", "postgres", "frontend")}
    current, calls, _ = environment(monkeypatch, states)
    result = maintenance.run_backup("isolated", tmp_path, execute=True)
    assert current == states
    assert ("start", "postgres-selected") in calls and ("stop", "postgres-selected") in calls
    assert ("start", "backend-selected") not in calls and ("start", "agent-selected") not in calls
    assert result["passed"] is True


def test_cli_returns_nonzero_and_structured_failure_instead_of_success(tmp_path, monkeypatch, capsys):
    report = {"passed": False, "errors": [{"step": "restore_start", "service": "backend", "error_type": "RuntimeError"}]}
    monkeypatch.setattr(maintenance, "run_backup", Mock(side_effect=maintenance.MaintenanceFailure(report)))
    output = tmp_path / "failure.json"
    assert maintenance.main(["backup", "--project", "isolated", "--execute", "--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().out) == report
    assert json.loads(output.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("url", ["https://external.example:18380", "http://127.0.0.1:9999", "http://localhost:18380/private", "http://user@localhost:18380"])
def test_recovery_rejects_external_or_misbound_url_before_probing_or_restarting(monkeypatch, url):
    _, calls, _ = environment(monkeypatch)
    probe = Mock(side_effect=AssertionError("Must reject URL before a probe"))
    monkeypatch.setattr(maintenance, "_container_probe", probe)
    with pytest.raises(ValueError):
        maintenance.recover_scheduler("isolated", url, execute=True)
    probe.assert_not_called()
    assert not any(call[0] == "restart" for call in calls)


def test_recovery_probes_and_restarts_only_the_selected_container(monkeypatch):
    _, calls, _ = environment(monkeypatch)
    probe = Mock(return_value=(503, {"code": "SCHEDULER_OWNERSHIP_LOST"}))
    monkeypatch.setattr(maintenance, "_container_probe", probe)
    result = maintenance.recover_scheduler("isolated", "http://127.0.0.1:18380", execute=True)
    probe.assert_called_once_with("backend-selected")
    assert ("restart", "--time", "30", "backend-selected") in calls
    maintenance._wait_ready.assert_called_once_with("backend-selected", "backend")
    assert result["ready_after_restart"] is True


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.0.0.2", "::1", "workstation", "workstation.example"])
def test_local_unc_aliases_are_not_configured_remote_targets(monkeypatch, host):
    monkeypatch.setattr(maintenance.socket, "gethostname", lambda: "workstation")
    monkeypatch.setattr(maintenance.socket, "getfqdn", lambda: "workstation.example")
    assert maintenance._remote_target("\\\\" + host + "\\backup") is False


def test_remote_unc_is_configuration_only_never_verified_failure_domain(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(maintenance, "validate_manifest", lambda _: {})
    monkeypatch.setattr(maintenance, "_remote_target", lambda _: True)
    result = maintenance.replicate(source, tmp_path / "replica")
    assert result["configured_remote_target"] is True
    assert result["verified"] is True
    assert result["off_host"] is False and result["failure_domain_verified"] is False
