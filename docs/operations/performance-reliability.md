# 性能、限流与雪崩保护

本文描述当前单机 Docker 部署的真实保护边界。离线 validation 压测用于验证架构行为，不能换算成生产 SLA、真实模型 QPS 或公网容量。

## 链路与容量舱壁

请求链路为：浏览器 → Nginx → Java 业务服务 → Python Agent → LLM／高德 MCP；Java 也会直接调用高德 REST 重新确认 POI 与路线事实。

| 层 | 默认保护 | 饱和行为 |
| --- | --- | --- |
| Nginx | 单客户端 32、整站 128 个并发连接 | 503，未进入 Java |
| Java HTTP | Tomcat 64 工作线程、32 等待槽、256 连接上限 | 有界排队，超出后由入口快速失败 |
| PostgreSQL | Hikari 最多 12、最少空闲 4、取连接最多等 3 秒；SQL 默认 5 秒 | 请求失败，不无限占住 HTTP 线程 |
| 规划任务 | 4 个 worker；正式默认队列 32、单用户活动任务 4 | `USER_QUEUE_FULL` 或 `TASK_QUEUE_FULL`（429） |
| Java→Agent | 8 个并发槽 | `AGENT_BUSY`（503） |
| Java→高德 REST | 8 个并发槽 | `AMAP_BUSY`（503），规划保留明确数据缺口 |
| Python 行程执行 | 4 个槽 | 429，由 Java 记录为 Agent 失败而不是继续堆积 |
| Python 研究／索引／解析 | 共 4 个独立槽 | 429，不挤占行程执行槽 |
| Python 高德工具 | 8 个独立槽 | 429，不挤占研究和行程执行槽 |

公网请求限流按“认证用户 + 归一化路由”计数；登录、注册才按客户端地址计数。因此同一 NAT 下的已登录用户不会互相占用额度。窗口之间没有全局锁，清理最多每分钟执行一次。当前是可靠单机设计，多实例部署必须把窗口迁移到 Redis 或网关，不能把进程内计数当作全局限流。

## 超时、熔断与降级

- 行程任务正式默认总截止时间 300 秒；截止后 Java 拒绝迟到结果并写入终态。
- 单日 LLM 默认 45 秒，超时使用已取得的可信 POI 草稿，不重试模型调用。
- 高德 MCP 默认 20 秒；Java 高德 REST 连接最多 2 秒、单次总计 10 秒；图片下载 8 秒；Agent 普通能力调用 30 秒。
- Java 对 Agent 和高德各自统计连续失败。默认连续 5 次失败后熔断 15 秒；熔断期快速返回，期满只放行一个半开探测，成功后恢复。
- 并发槽满时不等待慢请求释放，只等待最多 50 毫秒后快速失败。系统不会用自动重试放大已经过载的上游。
- Agent readiness 不访问模型或 Embedding；Java 对 readiness 成功缓存 1 秒、失败缓存 3 秒，避免上游宕机时形成健康探测风暴。
- Python 执行 ID 默认保留 15 分钟用于阻止重复生成，然后清理；活动执行不会被清理。

“熔断”只阻止故障继续扩散，不等于高可用：单机 Java、Agent 或数据库本身停止时，相关能力仍会不可用。传统行程查看、历史和其他不依赖 Agent 的接口应继续服务。

## 时间耗在哪里

从外到内按同一个 `X-Request-ID` 对齐：

1. Nginx JSON access log：`request_seconds` 是客户端总耗时，`upstream_seconds` 是 Java 耗时；两者差值主要是入口发送、客户端背压和连接等待。
2. Java Prometheus：`http_server_requests_seconds`、`tomcat_threads_*`、`hikaricp_connections_*` 判断是 HTTP 线程还是数据库池饱和。
3. Java 上游指标：`trip_upstream_call_duration_seconds`、`trip_upstream_rejected_total`、`trip_upstream_circuit_opened_total` 区分 Agent／高德慢、容量满和熔断。
4. 任务指标与日志：`trip_task_execution_duration_seconds`，以及 `task_execution_started/finished/failed` 给出持久任务总耗时。
5. Agent 指标：`trip_agent_http_request_seconds`、`trip_agent_http_in_flight`、`trip_agent_capacity_rejected_total`；规划内部继续看既有 `trip_agent_stage_seconds`、模型调用和 RAG 指标。
6. 每个已保存结果的 `quality.timings` 包含 Java POI 规范化、路线与规则校验耗时；它不包含浏览器和排队时间。

故障时最先出现的信号：任务队列满先返回 `TASK_QUEUE_FULL`；Agent 容量满先记录 `agent_capacity_rejected`；连续上游失败达到阈值时 Java 先记录 `upstream_circuit_open`，随后请求返回 `*_CIRCUIT_OPEN`，最后 Nginx access log记录对应 503 和分层耗时。

## 受控演练

只对隔离栈执行，脚本会拒绝非 validation 目标：

```powershell
$env:VALIDATION_SLOW_SECONDS='3'
docker compose -p trip-validation -f docker-compose.validation.yml up -d --build --wait --wait-timeout 180
python backend/scripts/performance_drill.py --output docs/evidence/performance-current/report.json
```

演练包含：数据库读请求 1/8/32/64 并发阶梯；24 个慢规划任务；慢任务期间 health/history 探针；停止 Agent；停止高德替身；验证熔断快速失败和半开恢复。任一读阶段错误率达到 5% 或 P95 达到 2 秒即停止继续升压。脚本最终恢复被停止的替身服务。

观察正式栈时只做只读检查，不运行离线故障命令：

```powershell
docker compose exec -T backend curl -fsS http://localhost:9000/actuator/prometheus
docker compose exec -T agent python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9000/metrics').status)"
docker compose logs --since 10m backend agent frontend
docker stats --no-stream
```
