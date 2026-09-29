# 受控性能与故障演练

> 离线 validation fixture 的单机架构证据；不是生产 SLA、真实模型吞吐或容量承诺。

- 生成时间：2026-09-29T02:28:39.665236+00:00
- 首个饱和信号：Java durable task queue

## 读请求阶梯

- 并发 1: 135.86 QPS, P95 0.01s, 错误率 0.00%
- 并发 8: 637.95 QPS, P95 0.0186s, 错误率 0.00%
- 并发 32: 394.99 QPS, P95 0.1947s, 错误率 0.00%
- 并发 64: 409.65 QPS, P95 0.3814s, 错误率 0.00%
- 并发 128: 387.7 QPS, P95 0.8096s, 错误率 0.00%
- 并发 256: 414.61 QPS, P95 1.3886s, 错误率 0.00%

## 慢任务隔离

- 提交 24，接受 12，状态 {'429': 12, '202': 12}，保护码 {'TASK_QUEUE_FULL': 12}。
- 慢任务期间 health P95 0.0331s；history P95 0.0245s。

## 上游故障

- Agent 停止：{'503': 16}，Java health=200。
- 高德替身停止首波：{'AMAP_BUSY': 12, 'MAP_UNAVAILABLE': 8}。
- 熔断波：{'AMAP_CIRCUIT_OPEN': 20}；恢复探测：{'status': 200, 'code': ''}。

## 日志顺序

- `backend-1       | 2026-09-29T02:28:03.635Z  WARN 1 --- [pool-2-thread-2] [request_id=06ab20a0c29d65c341bcf5745c77abf1] com.tripplanner.domain.TaskService       : task_execution_failed task_id=3fe77a05-5910-4479-969e-9b30aae3f295 execution_id=3a859bb5-21ee-4c54-88c1-ad0a7c35a111 code=TASK_TIMEOUT type=IOException elapsed_ms=9414`
- `backend-1       | 2026-09-29T02:28:03.635Z  WARN 1 --- [pool-2-thread-4] [request_id=aac06bfc27a74faabb43d5895ee94c3b] com.tripplanner.domain.TaskService       : task_execution_failed task_id=6c9640a1-00dc-4a7f-b603-5ec6358bb13e execution_id=4fb78127-c350-49ed-9c51-cf064649d880 code=TASK_TIMEOUT type=IOException elapsed_ms=8908`
- `backend-1       | 2026-09-29T02:28:03.670Z  WARN 1 --- [pool-2-thread-1] [request_id=6972f7f17a458e70b99e34a3fafda882] com.tripplanner.domain.TaskService       : task_execution_failed task_id=259de8fe-11fe-4636-b657-efe169a33d59 execution_id=e7e8a5b5-6b28-4603-a3ad-40050899cc8f code=TASK_TIMEOUT type=IOException elapsed_ms=9707`
- `backend-1       | 2026-09-29T02:28:03.680Z  WARN 1 --- [pool-2-thread-3] [request_id=43963c6f6ed4ff2f498b8dfb285fc32d] com.tripplanner.domain.TaskService       : task_execution_failed task_id=7c045aeb-b73b-4926-af2b-741b835cd1b1 execution_id=97eb9dd7-2004-45ea-837c-86fc0e49febd code=TASK_TIMEOUT type=IOException elapsed_ms=9206`
- `backend-1       | 2026-09-29T02:28:10.220Z  WARN 1 --- [io-9000-exec-15] [request_id=53e0efd7740ea49023f062f8b03b20e8] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=agent reason=HttpConnectTimeoutException open_ms=15000`
- `backend-1       | 2026-09-29T02:28:19.157Z  WARN 1 --- [io-9000-exec-15] [request_id=8c8447eb7f576e44b2979a83800e58bc] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=amap reason=ConnectException open_ms=15000`
- `backend-1       | 2026-09-29T02:28:39.656Z  INFO 1 --- [io-9000-exec-24] [request_id=e496d901b4320f37f3afe20ce4ff5a0f] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_recovered upstream=amap`
