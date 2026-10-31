# Java + Python Agent 单机运行手册

当前本机日常部署使用 Compose 项目 `langchain-trip-planner`，包含七个服务：

| 服务 | 用途 |
|---|---|
| `frontend` | Nginx 与 Vue 页面，本机入口 `127.0.0.1:8380` |
| `backend` | Java 业务服务、任务调度与业务数据库访问 |
| `agent` | Python / LangGraph 规划、检索与解析 |
| `postgres` | PostgreSQL 业务数据库 |
| `prometheus` | 指标与告警规则，本机入口 `127.0.0.1:19090` |
| `alertmanager` | 告警分组、去重与恢复通知 |
| `alert-receiver` | 本地持久告警收件箱 |

七服务配置位于 `.codex-optimization/daily-window/daily-release.compose.json`，私有环境文件位于同目录的 `runtime/`。这些本机文件不进入 Git。仓库根目录 `docker-compose.yml` 仍提供四个核心服务的首次部署模板；已有七服务环境继续使用原 Compose 文件，不能用首次部署命令替换它。

截至 2026-10-31，本机七个日常容器均已停止；业务数据和私有配置保留。自动备份与恢复入口继续停用，当前没有有效恢复集。Python 业务后端已经退出工作树，不能再使用旧入口、Alembic 或旧 SQLAlchemy 操作脚本。

## 配置与数据

下列环境文件均相对于 `.codex-optimization/daily-window/runtime/`；根目录四服务模板首次生成的对应文件位于 `deploy/runtime/`，两套路径不能混用。

- Java 配置：`business.env`；仅它持有运行时业务数据库和用户 JWT 配置。
- Java 地图配置：`business.env` 中的 `AMAP_REST_API_KEY` 与有界缓存 TTL；仅公开 POI／天气／路线、用户主动复核和图片走此通道，Agent 生成不再由 Java 二次核验或改排。
- Agent 配置：`agent.env`；模型、`AMAP_API_KEY`、embedding、视觉、内部密钥与固定 Java 地址，不含数据库凭据。首次配置脚本将源文件中的 `AMAP_MCP_API_KEY` 转入运行配置的 `AMAP_API_KEY`。
- PostgreSQL 配置：`postgres.env`；继续使用原数据库凭据。
- PostgreSQL：`langchain-trip-planner_java_business_data` 数据卷。
- 原文件：`backend/data/knowledge_uploads`，Java 管理。
- 向量：`backend/data/chroma`；模型／维度变更须受控重建。
- 操作账本：`backend/data/agent-runtime`；不是业务事实源，不能通过删除它重置调用预算或重放旧执行。
- 监控配置：`deploy/prometheus.yml`、`deploy/alerts.yml`、`deploy/alertmanager.yml`；沿用既有只读挂载。
- 监控数据卷：`langchain-trip-planner_daily_prometheus`、`langchain-trip-planner_daily_alerts`、`langchain-trip-planner_daily_alert_inbox`；指标、告警状态与收件箱独立持久化。

已有私有配置不重新生成。`prepare_java_deployment.py` 只创建新目录，新部署默认开启 workers、关闭验收预算模式；已有账号环境的 JWT Key 不可随意更换。

## 现有七服务部署操作

以下命令在项目根目录执行，显式选择现有七服务配置。查看状态和日志不会启动容器：

```powershell
$dailyCompose = '.codex-optimization/daily-window/daily-release.compose.json'
docker compose -p langchain-trip-planner -f $dailyCompose ps -a
docker compose -p langchain-trip-planner -f $dailyCompose logs --tail 100 backend agent frontend prometheus alertmanager alert-receiver
```

需要日常使用时，以下命令启动已有七个容器；暂停时停止全部七服务，保留配置、镜像、数据卷与绑定目录。当前维护状态为停止，仅在需要运行服务时执行 `start`：

```powershell
docker compose -p langchain-trip-planner -f $dailyCompose start
docker compose -p langchain-trip-planner -f $dailyCompose stop
```

修改运行环境文件后，`start` / `restart` 不会重新载入新配置；确认需要更新时，在同一项目和 Compose 文件下仅重建受影响服务，例如 `docker compose -p langchain-trip-planner -f $dailyCompose up -d --no-build --force-recreate agent`。应用升级须先准备并验证新镜像，再更新原配置中的相应镜像引用；保留现有环境文件、挂载、端口与运行限制。不要运行首次配置生成脚本、改用根目录 Compose，或使用 `down -v` 排查问题。

## 健康与故障检查

Java 存活检查不调用模型，就绪检查只验证数据库、迁移和本地必要配置；前端只依赖 Java 健康。Agent 就绪检查验证 AI 服务本身。停止 Agent 后必须复测登录、POI 搜索、收藏、手工行程、路线、历史、分享和导出；智能规划应返回 `AGENT_UNAVAILABLE`，而不是拖垮传统业务。容器“已启动”不是服务“已就绪”。

Java 重启中的运行任务标为 `PROCESS_INTERRUPTED`，用户显式重试，不自动重新计费生成。单实例 PostgreSQL advisory lock 不等于跨主机故障接管；数据库异常后应检查并重启唯一 Java 实例，不另起第二套 workers。

内部 Prometheus 从 Java 的 `/actuator/prometheus` 抓取 Actuator/Micrometer 指标；Nginx 对公网入口显式拒绝 `/actuator/*`、`/metrics` 和 `/internal/*`。`X-Request-ID` 会进入 Java MDC、任务记录、审计事件和 Java→Agent 请求头，排障时应以该字段关联日志，不把用户 ID、任务 ID或提示词作为指标标签。

## V4 数据完整性升级

`V4__business_integrity_versions_audit.sql` 会增加业务外键、状态约束、行程版本和审计事件。它在修改结构前先检查孤儿用户／资料关系与非法状态；命中时 Flyway 会明确失败，不删除、不改绑任何历史数据。正式升级前必须在恢复克隆中运行，并逐项确认以下查询结果：

```sql
SELECT r.id,r.user_id FROM trip_records r LEFT JOIN users u ON u.id=r.user_id WHERE u.id IS NULL;
SELECT t.id,t.user_id,t.record_id FROM trip_tasks t LEFT JOIN users u ON u.id=t.user_id WHERE u.id IS NULL;
SELECT c.id,c.user_id,c.active_trip_id FROM assistant_conversations c LEFT JOIN users u ON u.id=c.user_id WHERE u.id IS NULL;
SELECT d.id,d.submitted_by,d.reviewed_by FROM knowledge_documents d
  LEFT JOIN users submitter ON submitter.id=d.submitted_by
  LEFT JOIN users reviewer ON reviewer.id=d.reviewed_by
  WHERE submitter.id IS NULL OR (d.reviewed_by IS NOT NULL AND reviewer.id IS NULL);
```

本项目历史记录中曾保留“原用户已不存在”的迁移数据，因此不能把 V4 失败误判为程序故障，也不能临时关闭外键。应由数据所有者在备份后明确选择恢复原账号主体、导出并归档这些记录，或依法删除；完成后再重跑迁移。升级成功后，删除行程会事务性撤销分享、解绑会话、写 RAG 删除墓碑并删除含完整计划的版本快照，脱敏审计事件继续保留。

2026-09-17 的正式 V3→V4 升级保留了 18 条历史行程和相关 RAG 作业：为原用户 ID 1、2 建立不可登录的归档主体；4 条终态任务和 2 个会话按新外键的 `ON DELETE SET NULL` 语义解除已删除行程引用；清除 1 条源资料早已不存在的已完成摄取作业；修正 4 条旧时区写入造成的终态任务期限顺序。升级前、修复后迁移前及升级后的完整备份分别位于 `E:\project\trip-planner-backups\pre-v4-20260917-233408`、`repaired-pre-v4-20260917-233810` 和 `post-v4-20260917-234046`。Flyway 最终处于 V4，38 条既有行程均建立基线版本；这些一次性维护动作不可复制到其他数据库，其他环境必须重新检查实际数据并由数据所有者确认处理方式。

## 备份和恢复（当前停用）

`backups/BACKUP_DISABLED.json` 标记备份停用，Windows 任务 `TripPlanner-AutomatedBackup` 保持 Disabled；历史备份与离线恢复材料已退役，没有有效恢复集。当前日常维护不执行备份、恢复、计划任务安装或重新启用操作。

以下命令仅核查任务状态，不启动备份或业务容器：

```powershell
Get-ScheduledTask -TaskName TripPlanner-AutomatedBackup | Select-Object TaskName,State
```

源码仍保留 Java/Flyway-v2 备份恢复工具和历史[详细运维说明](final-planning.md)。工具设计覆盖业务库、上传、Chroma、账本、有效私有配置及镜像标识，从实际容器读取持久挂载和 Compose 文件；恢复时校验哈希并使用独立网络、数据库和目录中的禁用 workers 克隆。这些工具及历史验收记录不代表现在有可用备份。

未来如需恢复备份能力，应另行确认新的备份目标和恢复验收范围，再安排维护窗口；当前继续保留停用状态。私有配置与业务数据不提交 GitHub。

## 迁移历史与旧版本回滚

下述路径和文件名仅记录迁移历史，不代表当前资产存在或可用。2026-10-06 曾将外部备份目录移入项目的 `E:\project\langchain-trip-planner\backups`，当时核对哈希一致，并将历史材料合并为 `history-20260910-20260919.zip`；原始过程见[历史备份归档说明](backup-organization.md)。这些材料已随 2026-10-29 的备份退役处理，不能按旧路径尝试当前恢复。

原始数据为 9 用户、2 偏好、25 行程、4 任务、21 历史同步作业、2 资料、5 资料作业。18 行程原属已不存在的用户 1，经用户确认原样保留；其余 7 条按原所有者验收。没有重分配孤立记录或补造账号。

- 迁移前最终备份：`E:\project\trip-planner-backups\final-pre-java-20260915-1310`。
- 迁移后备份：`E:\project\trip-planner-backups\post-java-20260915-1331`。
- 恢复目录：`E:\project\trip-planner-backups\post-java-restored-20260915-1333`。
- 清理前源码：Git 提交 `6c72a24`；仓库外归档 `E:\project\trip-planner-backups\pre-cleanup-source-20260915.zip`。

旧迁移工具、旧 Compose 和旧业务源码仅作为历史资料，可从该提交或归档取回到**独立目录**。用户已决定不再回退 Python 架构，5 个旧架构回滚镜像已删除，不再提供现成旧镜像恢复路径。后续恢复以当前 Java＋Agent 的成套数据库、上传文件、索引和配置备份为准；绝不能让旧代码直接连接新版 `trip_java`。

## 临时验证环境

`docker-compose.validation.yml` / `docker-compose.migration.yml` 仅用于离线夹具验证；`trip-validation` 不作为第二套日常服务常驻。CI 创建后销毁；本地验证后按项目名 `down`，不执行全局 Docker prune。

清理验证资源时保留现有持久卷、宿主业务数据目录和私有配置；旧 Python 回滚镜像已另经用户确认删除。清理恢复克隆时只删除明确核对的克隆容器，不能按宽泛前缀误删日常七服务，更不能操作其他项目。

## 能力与测试归属

| 能力 | 当前实现 | 验证入口 |
|---|---|---|
| 登录、注销、偏好、账号隔离 | Java UsersController / Security | Java 测试、`java_api_contract_smoke.py` |
| 幂等、额度、任务、取消、超时 | Java TaskService + Agent 执行协议 | Java PostgreSQL 测试、HTTP 场景、重启／断流脚本 |
| 生成约束、预算、路线降级与质量分类 | Python Agent / `finalize_plan`；Java 仅做 `TrustedCandidates` 与协议校验 | pytest、共享协议、HTTP 业务场景 |
| 历史、编辑、改排、草稿应用 | Java HistoryController | 版本／所有者测试、Playwright |
| 原文件、审核、发布 | Java KnowledgeService + Agent extraction | `java_knowledge_smoke.py`、Agent 解析故障测试 |
| 向量同步与重建 | Java outbox + Agent indexing/rebuild | 稳定 ID、版本、墓碑、Java 快照冲突、检索回查 |
| 公开地图、用户主动复核与图片 | Java 高德 REST | Java 网关测试、缓存／错误映射、Agent 停机演练 |
| 旅游生成、路线、约束、模型、检索与解析 | Python Agent + 高德 MCP | pytest、MCP 协议、RAG／视觉故障用例 |
| 数据恢复与通知 | 当前 Java 运维脚本 | `java_recovery_drill.py`、`notification_smoke.py` |

清理前的 255 项 Python 测试包含已退役业务代码测试，不能用它与清理后的 Agent 测试数直接比较覆盖率。公开业务合同转移到 Java 和 HTTP 验收，不保留可运行的旧业务后端来维持测试计数。

## 付费能力与单机边界

本轮真实验收已结束，账本保留；整理阶段仅离线测试，不自动追加模型、高德或 embedding 调用。资料解析、索引重建和真实生成可能计费，执行前需另行确认预算。取消、超时或断流不保证已发出请求不计费。

本机日常七服务已包含 Prometheus、Alertmanager 与本地持久收件箱；本地接收不等于告警已送达真实值班人员。公开入口不暴露指标和内部服务。备份继续停用，真实人员通知、长期容量和多机高可用不属于当前验收范围，不能把本机验证作为生产 SLA。
