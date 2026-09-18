# 受控性能与故障演练

> 离线 validation fixture 的单机架构证据；不是生产 SLA、真实模型吞吐或容量承诺。

- 生成时间：2026-09-18T02:50:17.662462+00:00
- 首个饱和信号：Java durable task queue

## 读请求阶梯

- 并发 1: 43.17 QPS, P95 0.0362s, 错误率 0.00%
- 并发 8: 379.92 QPS, P95 0.0301s, 错误率 0.00%
- 并发 32: 496.03 QPS, P95 0.0952s, 错误率 0.00%
- 并发 64: 479.06 QPS, P95 0.193s, 错误率 0.00%

## 慢任务隔离

- 提交 24，接受 12，状态 {'429': 12, '202': 12}，保护码 {'TASK_QUEUE_FULL': 12}。
- 慢任务期间 health P95 0.0238s；history P95 0.0322s。

## 上游故障

- Agent 停止：{'503': 16}，Java health=200。
- 高德替身停止首波：{'AMAP_BUSY': 12, 'MAP_UNAVAILABLE': 8}。
- 熔断波：{'AMAP_CIRCUIT_OPEN': 20}；恢复探测：{'status': 200, 'code': ''}。

## 日志顺序

- `backend-1       | 2026-09-18T02:49:57.281Z  WARN 1 --- [io-9000-exec-20] [request_id=e8fd50d78f5b9edc89b21888729efc96] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=amap reason=HttpConnectTimeoutException open_ms=15000`
- `backend-1       | 2026-09-18T02:50:17.654Z  INFO 1 --- [nio-9000-exec-2] [request_id=c939fb8a8322e6ed14066db48488c8e8] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_recovered upstream=amap`
