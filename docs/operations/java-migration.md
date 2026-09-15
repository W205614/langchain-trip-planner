# Java/Python 单机迁移运行手册

适用当前 Java/Flyway 架构。旧 Python/Alembic 指令只能用于保留的旧栈。

## 迁移前

1. 从 `backend` 目录运行 `python scripts/migrate_java_business.py inspect`，确认生效数据库、数据路径和七张业务表数量。不要输出私有环境文件内容。
2. 保存旧镜像为 `langchain-trip-planner-backend:pre-java-migration`、`langchain-trip-planner-frontend:pre-java-migration`，确认镜像 ID。准备维护窗口；停止旧前端/后端写入，再执行 `backup --backup-dir <仓库外新目录>`。
3. 备份含一致性业务 JSON、上传/Chroma、旧私有配置、Compose 与镜像标识、文件 SHA-256 清单。运行 `verify` 校验；备份含密码哈希和密钥，应限制权限。
4. 先在独立 PostgreSQL 克隆库应用 Flyway、导入并验证数据；复制 Chroma 后检查集合/维度。不得直接用原库试迁移、自动 baseline、清空原索引或双写。

## 正式切换

1. 按 README 生成私有配置，Java `WORKERS_ENABLED=false`；启动新 `postgres/backend`，Flyway 初始化独立 `trip_java` 数据库。
2. 根目录执行 `python backend/scripts/import_java_daily.py --backup <最终备份>`。这个一次性管理容器临时接收目标数据库凭据，运行中的 Agent 没有这些凭据。
3. 导入工具拒绝非空库、未知列，保留 ID/哈希/JSON/时间/版本，修复序列，并逐表比较源字段。随后执行 `verify_java_restore.py --daily --backup <备份> --container langchain-trip-planner-backend-1 --output <报告>`，核对账号、行程与原文件。
4. 将 `WORKERS_ENABLED` 设为 `true`，启动 Agent、Java、前端；确认四容器健康及 `http://localhost:8080`。原账号密码登录由账号持有人验证，不用签发令牌的检查替代。
5. 验证旧数据、任务/SSE、修改冲突、解析复核/发布；失败保留新库、上传和日志。不要反向覆盖源库。

本次原数据：9 用户、2 偏好、25 行程、4 任务、21 历史同步作业、2 资料、5 资料作业。18 行程原属已不存在的用户 1，经用户确认原样保留；其余 7 条按原所有者验收。孤立记录不重新分配、不补造账号。

## 新架构维护备份和恢复

```powershell
docker compose stop frontend backend agent
python backend/scripts/backup_java_deployment.py --output E:\backups\trip-java-20260915
docker compose up -d --wait backend agent frontend
# 恢复到新数据库和仓库外新目录，不改变运行中的服务配置
python backend/scripts/restore_java_backup.py --backup E:\backups\trip-java-20260915 --output E:\backups\trip-java-restored
```

恢复工具先校验所有文件哈希、拒绝不安全归档成员；创建随机命名的独立数据库，以备份记录的 Java 镜像运行 worker-disabled 克隆，检查就绪并保留克隆供核对。回切前在维护窗口备份当前新数据，确认目标数据库/上传/Chroma/运行账本成套一致，再调整私有数据库 URL 和挂载目录。工具不会自动覆盖生产配置、删除现有数据或清零预算。

源数据库凭据变更后，恢复克隆需按当前数据库账号更新私有配置副本；不要改原备份。将备份移动到其他机器时必须先恢复对应镜像及私有网络/数据库环境。

## 回滚到旧架构

1. 停止新前端、Java、Agent；用新架构备份工具保留切换后的业务数据/原文件/Chroma/预算账本，另存容器日志。
2. 校验迁移前备份，恢复到独立旧版本数据源和数据目录；若原源库一直保持停止写入且逐表仍匹配，可使用该保留源库。上传/Chroma 必须与旧库快照配套，不能混用切换后索引。
3. 使用 `deploy/legacy-compose.yml`、保留的旧镜像和对应旧配置启动，核对原账号、行程/资料、健康后再开放 8080。
4. 不让旧代码连接 `trip_java`，不删除新库/失败日志，不把切换后的新数据静默丢弃。新增数据回迁需单独设计，不做自动双写。

## 运维边界

- Java PostgreSQL advisory lock 阻止正常启动第二个 worker；不支持多实例调度/跨机器故障接管。数据库连接异常后应停写并重启唯一 Java 实例，检查中断任务，不擅自启动第二套服务。
- Agent 使用本地进程锁、执行登记、索引命令账本；它们是操作状态，不是业务事实源。不能删除账本来重放旧任务或规避预算。
- `/metrics` 仅供内部 Prometheus。告警规则覆盖服务宕机、任务草稿/失败、索引积压和磁盘；本地通知接收器仅验收，不是实际运维通知渠道。生产告警需自行配置接收端。
- 索引重放从管理员接口读取当前业务版本；已删除/已变更资料的旧作业会跳过。解析/索引重建可能使用付费能力，确认预算后才执行。
- 本轮限额类别独立封顶：12 文本、1 视觉、50 embedding、100 高德。自动重试亦计数；已发出请求可能计费，不能承诺金额封顶。耗尽后保留账本并报告缺口。

## 协议与旧能力对照

| 旧 Python 业务能力 | 当前所有者 | 验证 |
|---|---|---|
| auth/preferences | Java UsersController/Security | bcrypt/JWT、真实 PG 集成、浏览器账号隔离 |
| trip_tasks/execution | Java TaskService + Python internal execution | 幂等竞争、取消/迟到、断流、重启、SSE 恢复 |
| planning_constraints/result_policy | Java PlanRules/TrustedCandidates | 原 12 个冻结约束场景、缺失/零值、HTTP 20 场景 |
| history/revision | Java HistoryController | If-Match、跨用户拒绝、草稿确认、编辑/导出浏览器测试 |
| knowledge/review | Java KnowledgeService + Python extraction | 原文件 ID/版本、损坏图像门禁、解析后人工复核、旧版本发布冲突 |
| rag_sync/index_rebuild | Java outbox + Python indexing/rebuild | 稳定 ID、删除墓碑、失败发布不重解析、快照冲突、可见性回查 |
| map/poi/research | Java 公开入口 → Python 能力 | HTTP 契约、MCP/REST/检索故障测试、有限真实样本 |

共享 Schema 位于 `contracts/internal-v1/`，通过 Python 导出脚本更新并由测试核对。公共 API 路径保持原前端调用；内部服务地址固定、独立密钥，Nginx 拒绝内部路径，Python 无宿主机端口。
