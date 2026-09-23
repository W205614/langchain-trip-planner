# 受控性能与故障演练

> 离线 validation fixture 的单机架构证据；不是生产 SLA、真实模型吞吐或容量承诺。

- 生成时间：2026-09-23T14:44:51.617298+00:00
- 首个饱和信号：history concurrency=256

## 读请求阶梯

- 并发 1: 274.8 QPS, P95 0.0045s, 错误率 0.00%
- 并发 8: 818.91 QPS, P95 0.0156s, 错误率 0.00%
- 并发 32: 540.19 QPS, P95 0.135s, 错误率 0.00%
- 并发 64: 563.51 QPS, P95 0.2801s, 错误率 0.00%
- 并发 128: 482.09 QPS, P95 0.616s, 错误率 0.00%
- 并发 256: 281.29 QPS, P95 2.1081s, 错误率 0.00%

## 慢任务隔离

- 提交 24，接受 12，状态 {'202': 12, '429': 12}，保护码 {'TASK_QUEUE_FULL': 12}。
- 慢任务期间 health P95 0.0268s；history P95 0.0299s。

## 上游故障

- Agent 停止：{'503': 16}，Java health=200。
- 高德替身停止首波：{'AMAP_BUSY': 12, 'MAP_UNAVAILABLE': 8}。
- 熔断波：{'AMAP_CIRCUIT_OPEN': 20}；恢复探测：{'status': 200, 'code': ''}。

## 日志顺序

- `backend-1       | 2026-09-23T14:43:34.472Z  INFO 1 --- [io-9000-exec-19] [request_id=0d0936e55984861efdcb88fa4de59e7d] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_recovered upstream=agent`
- `backend-1       | 2026-09-23T14:44:31.129Z  WARN 1 --- [nio-9000-exec-3] [request_id=f01741acb14737fc5e82221c576db2d0] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=amap reason=ConnectException open_ms=15000`
