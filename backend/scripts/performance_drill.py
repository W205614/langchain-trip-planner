"""Bounded full-stack load and dependency-failure drill.

The default target must be the isolated validation stack. It ramps only until a configured
latency/error threshold, uses offline fixtures, and restores stopped fixture services in finally
blocks. Fixture throughput is architecture evidence, not a production SLA or model benchmark.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx


TERMINAL = {"succeeded", "needs_attention", "failed", "cancelled"}


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 4)


def summarize(latencies: list[float], statuses: list[str], elapsed: float) -> dict:
    total = len(statuses)
    errors = sum(not status.startswith("2") for status in statuses)
    return {
        "requests": total,
        "qps": round(total / max(elapsed, 0.001), 2),
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
        "p99_seconds": percentile(latencies, 0.99),
        "error_rate": round(errors / total, 4) if total else None,
        "statuses": dict(Counter(statuses)),
    }


def request_stage(
    client: httpx.Client,
    method: str,
    path: str,
    concurrency: int,
    duration: float,
    **kwargs,
) -> dict:
    latencies: list[float] = []
    statuses: list[str] = []
    lock = threading.Lock()
    deadline = time.monotonic() + duration
    started = time.monotonic()

    def worker() -> None:
        local_latencies: list[float] = []
        local_statuses: list[str] = []
        while time.monotonic() < deadline:
            request_started = time.perf_counter()
            try:
                response = client.request(method, path, **kwargs)
                local_statuses.append(str(response.status_code))
            except httpx.TransportError as exc:
                local_statuses.append("transport:" + type(exc).__name__)
            local_latencies.append(time.perf_counter() - request_started)
        with lock:
            latencies.extend(local_latencies)
            statuses.extend(local_statuses)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(lambda _: worker(), range(concurrency)))
    result = summarize(latencies, statuses, time.monotonic() - started)
    result.update({"concurrency": concurrency, "duration_seconds": duration, "path": path})
    return result


def register(client: httpx.Client, prefix: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": f"{prefix}_{uuid.uuid4().hex[:10]}", "password": "performance123"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def one_shot(client: httpx.Client, requests: list[tuple[str, str, dict]]) -> dict:
    latencies: list[float] = []
    statuses: list[str] = []
    codes: list[str] = []

    def send(spec: tuple[str, str, dict]) -> None:
        method, path, kwargs = spec
        started = time.perf_counter()
        try:
            response = client.request(method, path, **kwargs)
            statuses.append(str(response.status_code))
            try:
                payload = response.json()
                codes.append(str(payload.get("code") or payload.get("error_code") or ""))
            except ValueError:
                codes.append("")
        except httpx.TransportError as exc:
            statuses.append("transport:" + type(exc).__name__)
            codes.append(type(exc).__name__)
        latencies.append(time.perf_counter() - started)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(64, len(requests))) as pool:
        list(pool.map(send, requests))
    result = summarize(latencies, statuses, time.monotonic() - started)
    result["codes"] = dict(Counter(filter(None, codes)))
    return result


def slow_task_scenario(client: httpx.Client, total: int = 24) -> dict:
    tokens = [register(client, "load") for _ in range(max(1, math.ceil(total / 4)))]
    body = {
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
        "travel_days": 1,
        "transportation": "步行",
        "accommodation": "经济",
        "preferences": [],
        "free_text_input": "fixture:slow",
    }
    requests = [
        (
            "POST",
            "/api/trip/tasks",
            {
                "headers": {**auth(tokens[index // 4]), "Idempotency-Key": str(uuid.uuid4())},
                "json": body,
            },
        )
        for index in range(total)
    ]
    task_ids: list[tuple[str, str]] = []

    def submit(spec: tuple[str, str, dict]) -> tuple[str, str, str]:
        _, path, kwargs = spec
        response = client.post(path, **kwargs)
        code = ""
        identity = ""
        try:
            payload = response.json()
            code = str(payload.get("code") or payload.get("error_code") or "")
            identity = str(payload.get("data", {}).get("id", ""))
        except ValueError:
            pass
        return str(response.status_code), code, identity

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=total) as pool:
        submitted = list(pool.map(submit, requests))
    for index, (_, _, identity) in enumerate(submitted):
        if identity:
            task_ids.append((identity, tokens[index // 4]))

    probe_token = tokens[0]
    health = request_stage(client, "GET", "/healthz", 8, 4)
    history = request_stage(
        client,
        "GET",
        "/api/history?page=1&page_size=10",
        8,
        4,
        headers=auth(probe_token),
    )

    terminal: Counter[str] = Counter()
    deadline = time.monotonic() + 45
    pending = dict(task_ids)
    while pending and time.monotonic() < deadline:
        for identity, token in list(pending.items()):
            response = client.get(f"/api/trip/tasks/{identity}", headers=auth(token))
            if response.status_code == 200:
                status = response.json()["data"]["status"]
                if status in TERMINAL:
                    terminal[status] += 1
                    pending.pop(identity, None)
        if pending:
            time.sleep(0.25)

    return {
        "submitted": len(submitted),
        "submission_statuses": dict(Counter(status for status, _, _ in submitted)),
        "submission_codes": dict(Counter(code for _, code, _ in submitted if code)),
        "accepted": len(task_ids),
        "terminal": dict(terminal),
        "still_pending_after_45s": len(pending),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "health_during_slow_work": health,
        "history_during_slow_work": history,
    }


def compose(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    base = ["docker", "compose", "-p", "trip-validation", "-f", "docker-compose.validation.yml"]
    return subprocess.run(base + command, check=check, capture_output=True, text=True)


def wait_service(service: str) -> None:
    compose(["up", "-d", "--wait", "--wait-timeout", "90", service])


def fault_scenarios(client: httpx.Client) -> dict:
    token = register(client, "fault")
    body = {
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
        "travel_days": 1,
        "transportation": "步行",
        "accommodation": "经济",
        "preferences": [],
        "free_text_input": "fixture:slow",
    }
    report: dict[str, dict] = {}
    try:
        compose(["stop", "agent"])
        time.sleep(1.5)
        specs = [
            (
                "POST",
                "/api/trip/tasks",
                {"headers": {**auth(token), "Idempotency-Key": str(uuid.uuid4())}, "json": body},
            )
            for _ in range(16)
        ]
        report["agent_down"] = one_shot(client, specs)
        report["agent_down"]["backend_health"] = client.get("/healthz").status_code
    finally:
        wait_service("agent")

    try:
        compose(["stop", "amap-fixture"])
        first = [
            ("GET", f"/api/map/weather?city=fault-{index}", {"headers": auth(token)})
            for index in range(20)
        ]
        report["amap_down_first_wave"] = one_shot(client, first)
        second = [
            ("GET", f"/api/map/weather?city=circuit-{index}", {"headers": auth(token)})
            for index in range(20)
        ]
        report["amap_down_circuit_wave"] = one_shot(client, second)
    finally:
        wait_service("amap-fixture")
    time.sleep(16)
    recovered = client.get("/api/map/weather?city=recovery", headers=auth(token))
    report["amap_half_open_recovery"] = {
        "status": recovered.status_code,
        "code": recovered.json().get("code", "") if recovered.content else "",
    }
    return report


def docker_snapshot() -> list[dict[str, str]]:
    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}|{{.NetIO}}|{{.BlockIO}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) == 6 and "trip-validation" in parts[0]:
            rows.append(dict(zip(("name", "cpu", "memory", "pids", "network", "block_io"), parts)))
    return rows


def relevant_logs(since: str) -> list[str]:
    result = compose(["logs", "--since", since, "backend", "agent", "amap-fixture"], check=False)
    needles = ("capacity", "circuit", "task_execution_failed", "agent_execution_failed", " 429 ", " 503 ")
    return [line for line in result.stdout.splitlines() if any(needle in line for needle in needles)][:100]


def markdown(report: dict) -> str:
    lines = [
        "# 受控性能与故障演练",
        "",
        "> 离线 validation fixture 的单机架构证据；不是生产 SLA、真实模型吞吐或容量承诺。",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 首个饱和信号：{report.get('first_saturation', '未达到停止阈值')}",
        "",
        "## 读请求阶梯",
        "",
    ]
    for stage in report["read_ramp"]:
        lines.append(
            f"- 并发 {stage['concurrency']}: {stage['qps']} QPS, P95 {stage['p95_seconds']}s, 错误率 {stage['error_rate']:.2%}"
        )
    slow = report["slow_tasks"]
    lines.extend(
        [
            "",
            "## 慢任务隔离",
            "",
            f"- 提交 {slow['submitted']}，接受 {slow['accepted']}，状态 {slow['submission_statuses']}，保护码 {slow['submission_codes']}。",
            f"- 慢任务期间 health P95 {slow['health_during_slow_work']['p95_seconds']}s；history P95 {slow['history_during_slow_work']['p95_seconds']}s。",
            "",
            "## 上游故障",
            "",
            f"- Agent 停止：{report['faults']['agent_down']['statuses']}，Java health={report['faults']['agent_down']['backend_health']}。",
            f"- 高德替身停止首波：{report['faults']['amap_down_first_wave']['codes']}。",
            f"- 熔断波：{report['faults']['amap_down_circuit_wave']['codes']}；恢复探测：{report['faults']['amap_half_open_recovery']}。",
            "",
            "## 日志顺序",
            "",
        ]
    )
    lines.extend(f"- `{line}`" for line in report.get("relevant_logs", [])[:20])
    return "\n".join(lines) + "\n"


def run(base_url: str, output: Path, duration: float) -> dict:
    marker = httpx.get(base_url + "/api/validation/fixture", timeout=5)
    if marker.status_code != 200 or marker.json().get("offline_fixture") is not True:
        raise SystemExit("Performance drill refuses non-validation targets")
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=128)
    with httpx.Client(base_url=base_url, timeout=35, limits=limits) as client:
        token = register(client, "read")
        ramp = []
        first_saturation = None
        for concurrency in (1, 8, 32, 64):
            stage = request_stage(
                client,
                "GET",
                "/api/history?page=1&page_size=10",
                concurrency,
                duration,
                headers=auth(token),
            )
            ramp.append(stage)
            if (stage["error_rate"] or 0) >= 0.05 or (stage["p95_seconds"] or 0) >= 2:
                first_saturation = f"history concurrency={concurrency}"
                break
        slow = slow_task_scenario(client)
        if not first_saturation and slow["submission_codes"]:
            first_saturation = "Java durable task queue"
        faults = fault_scenarios(client)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "isolated_offline_fixture",
        "boundary": "single-host offline fixture; not production SLA, real provider QPS, or model latency",
        "read_ramp": ramp,
        "slow_tasks": slow,
        "faults": faults,
        "first_saturation": first_saturation,
        "docker_snapshot": docker_snapshot(),
        "relevant_logs": relevant_logs(since),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage-seconds", type=float, default=3)
    args = parser.parse_args()
    result = run(args.base_url, args.output, max(1, min(args.stage_seconds, 10)))
    print(json.dumps({"first_saturation": result["first_saturation"]}, ensure_ascii=False))
