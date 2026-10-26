"""Evidence-only probe, to be executed explicitly inside the final uvicorn Agent.

Example (container-local command, executed by the operator):
  python /tmp/hardened-runtime-probe.py --require-read-only-rootfs \
    --output /app/runtime/hardened-runtime-probe-UNIQUE.json

No Docker calls, business PostgreSQL access, model, map or remote embeddings.
The only HTTP request is the real PID 1 Agent's loopback /readyz. Local Chroma
uses a unique independent hidden database under the Chroma mount and explicit
deterministic replacement embeddings, so its tiny files can join a mount snapshot.
Probe files and tiny collections are retained; no running index is reset/deleted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
from uuid import uuid4


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def proc_status(pid):
    values = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"CapBnd", "CapEff", "NoNewPrivs", "Uid", "Gid"}:
            values[key] = value.strip()
    return values


def process_boundary():
    command = [word.decode("utf-8", "replace") for word in Path("/proc/1/cmdline").read_bytes().split(b"\0") if word]
    require(any(Path(word).name == "uvicorn" for word in command), "PID 1 is not an actual uvicorn command")
    require("app.agent_api.main:app" in command, "PID 1 does not serve the production Agent application")
    pid1, probe = proc_status(1), proc_status("self")
    for label, values in (("PID 1", pid1), ("probe", probe)):
        require(int(values["CapBnd"], 16) == 0, f"{label} capability bounding set is not zero")
        require(int(values["CapEff"], 16) == 0, f"{label} effective capabilities are not zero")
        require(values["NoNewPrivs"] == "1", f"{label} no-new-privileges is not enabled")
    require(os.geteuid() == int(pid1["Uid"].split()[1]), "Run the probe as the Agent's actual effective UID")
    require(os.environ.get("PYTHONDONTWRITEBYTECODE") == "1", "PYTHONDONTWRITEBYTECODE must be 1")
    return {"pid1_cmdline": command, "pid1_status": pid1, "probe_status": probe,
            "probe_effective_uid": os.geteuid(), "python_bytecode_writes_disabled": True}


def mount_table():
    result = []
    unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        result.append({"target": unescape(fields[4]), "options": fields[5].split(","),
                       "filesystem": right.split()[0]})
    return result


def containing_mount(target):
    value = str(Path(target).resolve())
    matches = [item for item in mount_table() if value == item["target"] or
               value.startswith(item["target"].rstrip("/") + "/")]
    require(bool(matches), "Could not resolve the target's actual mount")
    return max(matches, key=lambda item: len(item["target"]))


def rootfs_boundary(required):
    mount = next(item for item in mount_table() if item["target"] == "/")
    read_only = "ro" in mount["options"]
    if required:
        require(read_only, "Root filesystem is writable although read-only was required")
    return {"read_only": read_only, "read_only_required": required, "mount": mount,
            "boundary": "Observed mount flags; no write attempt to the application/root filesystem."}


def writable_directory(env_key, expected, probe_id):
    if env_key:
        require(os.environ.get(env_key) == expected, f"{env_key} must identify the expected isolated mount")
    directory = Path(expected)
    require(directory.is_dir(), f"Expected writable directory does not exist: {expected}")
    folder = directory / (".hardened-runtime-probe-" + probe_id)
    folder.mkdir(mode=0o700, exist_ok=False)
    destination = folder / "write-readback.bin"
    content = ("container-local-probe:" + probe_id).encode()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    require(destination.read_bytes() == content, f"Write/readback failed for {expected}")
    return {"directory": expected, "retained_file": str(destination), "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "mount": containing_mount(directory),
            "readback_verified": True}


def restricted_tools(script, manifest):
    require(Path(script).is_file(), "Restricted-tools checker missing; build the reviewed checker into this image")
    require(Path(manifest).is_file(), "Restricted-tools image manifest missing")
    result = subprocess.run([sys.executable, script, "--check", "--manifest", manifest],
                            capture_output=True, text=True, timeout=60, check=False)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("Restricted-tools checker returned invalid JSON") from None
    require(result.returncode == 0 and data.get("status") == "verified_absent",
            "Restricted-tools names/aliases/copies/filecaps check did not verify absence")
    return {"status": data["status"], "checker": script, "manifest": manifest,
            "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
            "checker_result": data,
            "boundary": "Delegates mount/umount/nsenter/unshare names, aliases, matching copies and filecap checks to the reviewed image checker; no claim about libmount/library-code absence."}


def infocmp_path_check():
    bins = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/local/sbin")
    present = [str(Path(folder) / "infocmp") for folder in bins if os.path.lexists(Path(folder) / "infocmp")]
    require(not present and shutil.which("infocmp") is None, "The removed infocmp CLI is still available")
    return {"known_paths_absent": True,
            "boundary": "Known names/PATH only; arbitrary renamed infocmp copies require the separate component evidence, not this limited path check."}


def real_readiness():
    # Literal loopback URI only; no external redirects or caller-provided URL.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError("Agent readiness unexpectedly redirected")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open("http://127.0.0.1:9000/readyz", timeout=10) as response:
        require(response.status == 200, "Live Agent readiness is not HTTP 200")
        body = response.read(16385)
        require(len(body) <= 16384, "Readiness body exceeds the probe limit")
        data = json.loads(body)
    require(data.get("service") == "trip-agent" and data.get("status") == "ready",
            "Loopback readiness is not the expected actual Agent service")
    return {"url": "http://127.0.0.1:9000/readyz", "status": 200,
            "service": data["service"], "rag_status_from_running_process": data.get("rag"),
            "body_sha256": hashlib.sha256(body).hexdigest()}


def deny_python_network():
    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
            raise RuntimeError("Network forbidden during the local component probe")
    sys.addaudithook(audit)
    # This is a Python-level guard, not an OS egress firewall for native code.


def chroma_component(mode, directory, identity):
    deny_python_network()
    sys.path.insert(0, "/app")
    from langchain_chroma import Chroma
    from app.services.chroma_boundary import embedded_settings

    class DeterministicEmbeddings:
        def embed_documents(self, values):
            return [[1.0, 0.0, 0.0] if "预约" in value else [0.0, 1.0, 0.0] for value in values]

        def embed_query(self, value):
            return self.embed_documents([value])[0]

    store = Chroma(collection_name="hardened_probe_" + identity,
                   embedding_function=DeterministicEmbeddings(), persist_directory=directory,
                   client_settings=embedded_settings(directory))
    doc_id = "probe_" + identity
    text = "容器探针预约原句；仅用于本地持久检索验证，不是旅行事实。"
    if mode == "write":
        store.add_texts([text, "容器探针餐饮原句；不是旅行事实。"], ids=[doc_id, doc_id + "_meal"],
                        metadatas=[{"probe_id": identity, "source": "deterministic_probe_not_official"}] * 2)
        return {"mode": mode, "stored_count": store._collection.count(),
                "database_directory": directory, "probe_id": identity}
    stored = store.get(ids=[doc_id])
    require(stored["documents"] == [text], "Fresh-process Chroma readback differs from persisted text")
    result = store.similarity_search("预约", k=1)
    require(len(result) == 1 and result[0].page_content == text and
            result[0].metadata["probe_id"] == identity, "Local vector retrieval did not return the persisted original")
    return {"mode": mode, "stored_count": store._collection.count(), "readback_verified": True,
            "retrieval_verified": True, "collection": "hardened_probe_" + identity,
            "database_directory": directory, "probe_id": identity,
            "boundary": "Explicit deterministic-embedding component snapshot readback; not full PostgreSQL disaster recovery or real-provider RAG verification."}


def persistent_chroma_roundtrip(probe_id):
    require(os.environ.get("CHROMA_DIR") == "/app/data/chroma", "CHROMA_DIR must identify the expected mounted index directory")
    probe_directory = Path("/app/data/chroma") / (".hardened-chroma-probe-" + probe_id)
    probe_directory.mkdir(mode=0o700, exist_ok=False)
    directory = str(probe_directory)
    observed = []
    for mode in ("write", "read"):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--chroma-child", mode,
                                 "--chroma-directory", directory, "--probe-id", probe_id],
                                capture_output=True, text=True, timeout=90, check=False,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            child = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"Chroma {mode} subprocess did not return a structured result") from None
        require(result.returncode == 0 and child.get("passed") is True,
                f"Chroma {mode} subprocess failed: {child.get('error_type', 'unknown')}: {child.get('message', '')}")
        observed.append(child["data"])
    sqlite = Path(directory) / "chroma.sqlite3"
    require(sqlite.is_file() and sqlite.stat().st_size > 0, "Chroma persistent SQLite file was not created")
    return {"retained_database_directory": directory, "probe_id": probe_id, "sqlite_bytes": sqlite.stat().st_size,
            "separate_process_write_then_read": True, "operations": observed,
            "replacement": "Explicit deterministic 3D embedding function; no model or remote embedding calls.",
            "scope": "Real local LangChain Chroma storage/retrieval adapter with explicit deterministic embeddings. Its independent subdirectory may be snapshotted/restored; not Java publication/visibility, full PostgreSQL disaster recovery, real-provider RAG or full answer-quality verification.",
            "network_guard": "Python socket connect/getaddrinfo forbidden in each child; not a native-code egress firewall.",
            "retention": "No collection/database deletion; the unique hidden subdirectory remains inside the Chroma data mount, separate from the running root index."}


def small_ortools_solve():
    deny_python_network()
    import ortools
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    first = model.NewIntVar(0, 60, "first")
    first_end = model.NewIntVar(0, 60, "first_end")
    second = model.NewIntVar(0, 60, "second")
    second_end = model.NewIntVar(0, 60, "second_end")
    intervals = [model.NewIntervalVar(first, 15, first_end, "first_interval"),
                 model.NewIntervalVar(second, 20, second_end, "second_interval")]
    model.AddNoOverlap(intervals)
    model.Add(first >= 5)
    model.Add(second >= first_end)
    model.Add(second_end <= 50)
    model.Minimize(second_end)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = 5
    status = solver.Solve(model)
    require(status == cp_model.OPTIMAL, "Native OR-Tools small scheduling solve is not optimal")
    values = [solver.Value(first), solver.Value(first_end), solver.Value(second), solver.Value(second_end)]
    require(values == [5, 20, 20, 40], "Native solve returned an unexpected feasible schedule")
    return {"ortools_version": ortools.__version__, "native_solver_status": "OPTIMAL",
            "intervals": [[values[0], values[1]], [values[2], values[3]]],
            "scope": "Small actual CP-SAT scheduling solve; no route provider or full itinerary feasibility claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-read-only-rootfs", action="store_true")
    parser.add_argument("--restricted-check-script", default="/app/scripts/restrict_runtime_tools.py")
    parser.add_argument("--restricted-manifest", default="/usr/local/share/tripplanner/restricted-runtime-tools.json")
    parser.add_argument("--chroma-child", choices=("write", "read"), help=argparse.SUPPRESS)
    parser.add_argument("--chroma-directory", help=argparse.SUPPRESS)
    parser.add_argument("--probe-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.chroma_child:
        try:
            require(bool(re.fullmatch(r"[a-f0-9]{32}", args.probe_id or "")), "Invalid probe identity")
            require(os.environ.get("CHROMA_DIR") == "/app/data/chroma", "CHROMA_DIR must identify the expected mounted index directory")
            expected = Path("/app/data/chroma") / (".hardened-chroma-probe-" + args.probe_id)
            actual = Path(args.chroma_directory or "")
            require(actual == expected and actual.resolve() == expected,
                    "Only this probe ID's exact independent Chroma subdirectory is allowed")
            require(actual.is_dir() and not actual.is_symlink(), "Independent probe database directory must already exist without a symlink")
            if args.chroma_child == "read":
                require((actual / "chroma.sqlite3").is_file(), "Restored probe SQLite database must already exist; refusing to create an empty replacement")
            else:
                require(not any(actual.iterdir()), "Initial probe write requires its new empty independent directory")
            data = chroma_component(args.chroma_child, args.chroma_directory, args.probe_id)
            print(json.dumps({"passed": True, "data": data})); return 0
        except Exception as error:
            print(json.dumps({"passed": False, "error_type": type(error).__name__, "message": str(error)[:400]})); return 1
    identity = uuid4().hex
    destination = args.output or Path("/app/runtime") / ("hardened-runtime-probe-" + identity + ".json")
    require(destination.is_absolute() and any(destination.resolve().is_relative_to(Path(folder))
            for folder in ("/app/runtime", "/app/logs", "/tmp")), "Report must use a private writable container-local path")
    report = {"probe_id": identity, "started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "boundary": "Container-local hardening and component checks only; no Docker, business PostgreSQL, map/model/remote embedding request or running Chroma reset.",
              "checks": [], "data": {}}

    def check(name, operation):
        try:
            report["data"][name] = operation()
            report["checks"].append({"name": name, "passed": True})
        except Exception as error:
            report["checks"].append({"name": name, "passed": False, "error_type": type(error).__name__,
                                     "message": str(error)[:600]})

    check("actual_uvicorn_privilege_boundary", process_boundary)
    check("root_filesystem", lambda: rootfs_boundary(args.require_read_only_rootfs))
    for env_key, folder in (("DATA_DIR", "/app/runtime"), ("CHROMA_DIR", "/app/data/chroma"), ("LOG_DIR", "/app/logs"), (None, "/tmp")):
        check("writable_" + (env_key or "tmp"), lambda key=env_key, path=folder: writable_directory(key, path, identity))
    check("restricted_tools_aliases_copies_filecaps", lambda: restricted_tools(args.restricted_check_script, args.restricted_manifest))
    check("legacy_infocmp_known_paths", infocmp_path_check)
    check("actual_agent_readiness", real_readiness)
    check("persistent_independent_chroma_roundtrip", lambda: persistent_chroma_roundtrip(identity))
    check("native_ortools_small_scheduling_solve", small_ortools_solve)
    report["passed"] = all(item["passed"] for item in report["checks"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    # Exclusive/private output: never overwrite previous evidence. All retained
    # probe files were newly created; no running service data is removed.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({"passed": report["passed"], "report": str(destination),
                      "failed_checks": [item["name"] for item in report["checks"] if not item["passed"]]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
