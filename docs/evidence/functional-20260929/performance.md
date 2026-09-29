# 受控性能与故障演练

> 离线 validation fixture 的单机架构证据；不是生产 SLA、真实模型吞吐或容量承诺。

- 生成时间：2026-09-29T02:21:03.877626+00:00
- 首个饱和信号：Java durable task queue

## 读请求阶梯

- 并发 1: 184.36 QPS, P95 0.0065s, 错误率 0.00%
- 并发 8: 649.47 QPS, P95 0.0189s, 错误率 0.00%
- 并发 32: 395.09 QPS, P95 0.2053s, 错误率 0.00%
- 并发 64: 369.98 QPS, P95 0.4275s, 错误率 0.00%
- 并发 128: 384.12 QPS, P95 0.7603s, 错误率 0.00%

## 慢任务隔离

- 提交 24，接受 12，状态 {'202': 12, '429': 12}，保护码 {'TASK_QUEUE_FULL': 12}。
- 慢任务期间 health P95 0.0349s；history P95 0.0249s。

## 上游故障

- Agent 停止：{'503': 16}，Java health=200。
- 高德替身停止首波：{'AMAP_BUSY': 12, 'MAP_UNAVAILABLE': 8}。
- 熔断波：{'AMAP_CIRCUIT_OPEN': 20}；恢复探测：{'status': 200, 'code': ''}。

## 日志顺序

- `backend-1       | 2026-09-29T02:20:27.642Z  WARN 1 --- [pool-2-thread-4] [request_id=a1b2bf986852db45e40b260f6b11bc5f] com.tripplanner.domain.TaskService       : task_execution_failed task_id=b176a202-16fb-4835-940d-bac68756c33b execution_id=4f1b40be-8458-444f-a96d-a3b27725fd9e code=AGENT_CONNECTION_LOST type=IOException elapsed_ms=9511`
- `backend-1       | 2026-09-29T02:20:27.644Z  WARN 1 --- [pool-2-thread-2] [request_id=044dd23f24a07fe142d71f2310925d97] com.tripplanner.domain.TaskService       : task_execution_failed task_id=685545bf-dc4c-454c-baee-58bdb840220a execution_id=7ec18ed1-1f92-4a2e-898e-d6db39a6235c code=AGENT_CONNECTION_LOST type=IOException elapsed_ms=9006`
- `backend-1       | 2026-09-29T02:20:27.684Z  WARN 1 --- [pool-2-thread-3] [request_id=44597d7ff1c3e5c2312966eb450048c0] com.tripplanner.domain.TaskService       : task_execution_failed task_id=d74fdd3f-c194-4461-8d5b-8a939deb9b4c execution_id=577d526d-1d35-4237-8979-c8e39f50dd45 code=AGENT_CONNECTION_LOST type=IOException elapsed_ms=9806`
- `backend-1       | 2026-09-29T02:20:27.690Z  WARN 1 --- [pool-2-thread-1] [request_id=cd40f839041bddacc6d2ed043a8ae641] com.tripplanner.domain.TaskService       : task_execution_failed task_id=77ec8157-8168-40e2-894d-b010d6400da1 execution_id=c880e60c-8add-43f1-b564-b384559e1196 code=AGENT_CONNECTION_LOST type=IOException elapsed_ms=9306`
- `backend-1       | 2026-09-29T02:20:34.412Z  WARN 1 --- [io-9000-exec-18] [request_id=1c0b19bc39bae8d4369360c07c7e5b34] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=agent reason=HttpConnectTimeoutException open_ms=15000`
- `backend-1       | 2026-09-29T02:20:43.500Z  WARN 1 --- [io-9000-exec-18] [request_id=f29f8f0799012e539751f510460ed20a] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_open upstream=amap reason=ConnectException open_ms=15000`
- `backend-1       | 2026-09-29T02:21:03.871Z  INFO 1 --- [io-9000-exec-13] [request_id=05d29b765eb6e1c49e504e236a0e7a36] c.tripplanner.resilience.UpstreamGuard   : upstream_circuit_recovered upstream=amap`
