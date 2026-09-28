# 助手改排提案：本地验收与证据边界

记录日期：2026-09-28。测试对象为本地工作树，Java 使用隔离 PostgreSQL 17.6，Agent 结果由固定替身提供；前端交互使用 Playwright 拦截接口。以下数字是测试结果，不代表真实模型、地图或多用户生产表现。

## 闭环及可核验字段

1. 助手请求由 Java 读取原行程的当前版本，并将服务端会话 ID 写入任务；Python 继续产生规划与质量结果。
2. Java 校验结果协议、日期结构和可信 POI 后，写入独立提案记录。任务结果含提案 `id`、`version`、`quality.assistant_proposal_status=pending`、`quality.validated_outcome`、`quality.revision_parent.record_id/version`、问题和降级原因。原行程版本与索引同步队列不变。
3. 确认接口 `POST /api/assistant/conversations/{conversationId}/proposals/{proposalId}/confirm` 和放弃接口 `POST .../discard` 都要求 `If-Match` 为提案版本，并核验用户和会话。确认用原行程快照版本做条件更新；提案状态、原行程更新、审计版本和索引任务在同一事务内提交。放弃只更新提案。重复调用返回冲突。
4. 普通历史编辑、草稿应用、分享、广场投稿、执行工作台和助手再改排均不能把提案当原行程使用；待确认提案须先确认或放弃才能删除。普通手工改单日仍按原入口直接更新。

可按 `trip_tasks.id` 关联 `record_id`，查询任务的 `usage_json`、失败代码与任务耗时指标；在 `trip_records.quality_json` 查询规则结果、可信来源检查后的方案、降级原因、确认状态；在 `trip_record_versions` 查看提案生成、确认或放弃，以及原行程确认写入；在 `rag_sync_jobs` 查看确认后的索引任务。Micrometer 指标为 `trip.assistant.proposal.total{action=created|confirmed|discarded}`、`trip.plan.total{outcome=complete|degraded|draft}`、`trip.task.execution.duration`。这些计数按提交成功后增加；实际用户确认率需在指定时间窗内按持久记录计算，不能把本地测试点击当真实用户收益。

## 本地验证

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| Flyway V1–V9 | 通过 | 全新库与已有 V8 库升级到 V9；V9 仅扩展审计类型约束，不新增业务表 |
| Java `mvnw test` | 74 个测试通过，0 失败、0 跳过 | 含 4 个新提案集成测试；隔离 PostgreSQL、固定 Agent 替身 |
| 新前端提案用例 | 2 个通过 | 预览、确认的版本头与记录切换、放弃后返回列表 |
| 前端构建 | 通过 | `vue-tsc` 和 Vite 生产构建 |
| 隔离栈业务冻结场景 | 22/22 通过，见 [原始结果](frozen-business.json) | HTTP、Python Agent 离线替身、Java、PostgreSQL |
| 隔离栈助手提案闭环 | 通过，见 [原始结果](offline-http.json) | 原行程生成→意图识别→改排→可信校验→独立提案→确认→重复确认拒绝 |
| Java 服务重启后确认 | 通过，见 [重启结果](restart-http.json) | 提案持久化后重启 backend，再确认；原行程版本只增加一次 |
| 隔离栈 Playwright | 22/22 通过 | 含 2 个新增提案交互用例，浏览器连接完整的隔离验证栈 |

新 Java 用例覆盖：未确认原行程不变、质量分类与降级原因保留、可信 POI 拒绝、用量入库、确认一次、重复点击、放弃、跨用户、跨会话、提案与原行程版本冲突、草稿应用、待确认删除和发布入口拒绝、手工改排隔离。任务耗时及提案确认计数也被断言。用例将任务推进到实际结果处理、数据库事务和 HTTP 确认接口；规划内容由替身提供，不能据此证明真实模型满足硬约束。冻结规划与规则场景继续由现有 Python 评测和 CI 运行。

全量 Playwright 以隔离验证栈和 CI 同款离线地图 key 执行。单独连接未启动 Java/Agent 的 Vite 服务会导致全栈用例失败，且地图用例需要该测试 key；这些环境缺项不计作隔离栈通过证据。隔离栈的助手提案案例结果为 `degraded`：规则通过，保留餐饮可信候选缺失、开放与预约等降级原因；记录了 3 个 POI 和替身上报的用量 100 输入 token、100 输出 token。该用量是替身数据。

重启用例在脚本打印 `proposal_ready_for_backend_restart` 后执行 `docker compose -p trip-validation -f docker-compose.validation.yml restart backend`，待服务恢复后脚本才调用确认接口。它验证同一用户和提案的持久化收口；未测试多机切换、生产数据库恢复或真实模型中断。

## 尚需真实抽样

本次没有调用真实模型或高德，也没有得出真实用户确认率、完整行程质量或并发容量结论。独立抽样时先固定最多 3 个不同约束的单日改排案例、模型和地图各自的调用上限，并保留失败样本。逐例记录请求约束、提案质量、可信 POI、降级原因、模型输入/输出用量、任务耗时、是否确认和确认后原行程版本。若发现同类约束遗漏反复出现，先定位规则或上下文缺口；针对性重试须有次数和时间上限，并在同一批案例上比较改进前后结果。
