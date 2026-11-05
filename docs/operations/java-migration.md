# Java 业务服务迁移与升级

## 配置与数据

见[单机运行手册 · 配置与数据](single-host.md#配置与数据)。

## 现有七服务部署操作

见[单机运行手册 · 现有七服务部署操作](single-host.md#现有七服务部署操作)。

## 健康与故障检查

见[单机运行手册 · 健康与故障检查](single-host.md#健康与故障检查)。

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

## 升级与全新部署

仓库默认 `docker-compose.yml` 提供四核心服务模板；既有部署升级必须继续使用原 Compose 文件、项目名、私有配置与挂载。本机七服务使用 `.codex-optimization/daily-window/daily-release.compose.json`，不切换到根目录模板。`docker-compose.production.yml` 是独立的新环境，默认项目名为 `trip-production`，读取 `deploy/runtime-production/*.env`，不能通过换 Compose 文件来迁移已有资料。新环境先执行：

```powershell
python backend/scripts/prepare_java_deployment.py --output deploy/runtime-production
docker compose -f docker-compose.production.yml --profile monitoring up -d --build --wait
```

新配置会分配 `DATASET_ID`。业务数据库、上传与 Agent 持久目录的标记不匹配会拒绝启动。该标记证明环境身份，不能单独证明恢复时点一致；恢复和升级仍须核对成套备份的内容。

既有数据首次启用登记时，应在维护窗口停止写入，保留 PostgreSQL 可读，先成套备份，再执行检查。以下仅适用于尚未登记的数据集，示例路径必须替换为实际备份；本机七服务已完成登记且备份停用，不执行这组初始化或备份命令：

```powershell
python backend/scripts/backup_java_deployment.py --output backups/pre-optimization-YYYYMMDD-HHMM --archive-images
python backend/scripts/enroll_dataset.py --backup backups/pre-optimization-YYYYMMDD-HHMM --runtime deploy/runtime
# 上一步校验原表原列与三套持久文件后，显式登记同一数据集：
python backend/scripts/enroll_dataset.py --backup backups/pre-optimization-YYYYMMDD-HHMM --runtime deploy/runtime --execute
```

登记后还须准备本次私有运行配置和镜像，再使用明确的 Compose 文件启动。现有 `prepare_java_deployment.py --upgrade-existing` 会优先读取 `backend/.env` 的地图配置并选择 MCP 传输，使用前必须核对这是否符合原环境；本轮日常升级从原容器的有效配置准备新的私有文件，保留原供应商、凭据、传输和预算，没有直接运行该命令。实际镜像与挂载见[脱敏部署记录](../evidence/optimization-20261026/daily-effective-deployment-public.json)。不要为修复登记失败切换空卷、清库或删除原目录。旧镜像连接已迁移数据库不等于完成数据回退；应从已验证备份恢复到新的隔离数据库和文件目录。恢复工具保留旧、新克隆并报告精确资源，不自动清理。
