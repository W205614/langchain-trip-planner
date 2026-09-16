# Java + Python Agent 单机运行手册

当前日常部署只保留 Compose 项目 `langchain-trip-planner` 的四个服务：`frontend`、`backend`（Java）、`agent`、`postgres`。Python 业务后端已经退出工作树，不能再使用旧入口、Alembic 或旧 SQLAlchemy 操作脚本。

## 配置与数据

- Java 配置：`deploy/runtime/business.env`；仅它持有运行时业务数据库和用户 JWT 配置。
- Java 地图配置：`deploy/runtime/business.env` 中的 `AMAP_REST_API_KEY` 与有界缓存 TTL；公开 POI、天气、路线、图片和最终核验都走此通道。
- Agent 配置：`deploy/runtime/agent.env`；模型、`AMAP_MCP_API_KEY`、embedding、视觉、内部密钥与固定 Java 地址，不含数据库凭据。
- PostgreSQL：`langchain-trip-planner_java_business_data` 数据卷。
- 原文件：`backend/data/knowledge_uploads`，Java 管理。
- 向量：`backend/data/chroma`；模型／维度变更须受控重建。
- 操作账本：`backend/data/agent-runtime`；不是业务事实源，不能通过删除它重置调用预算或重放旧执行。

已有私有配置不重新生成。`prepare_java_deployment.py` 只创建新目录，新部署默认开启 workers、关闭验收预算模式；已有账号环境的 JWT Key 不可随意更换。

## 健康与故障检查

```powershell
docker compose ps
docker compose logs --tail 100 backend agent
```

Java 存活检查不调用模型，就绪检查只验证数据库、迁移和本地必要配置；前端只依赖 Java 健康。Agent 就绪检查验证 AI 服务本身。停止 Agent 后必须复测登录、POI 搜索、收藏、手工行程、路线、历史、分享和导出；智能规划应返回 `AGENT_UNAVAILABLE`，而不是拖垮传统业务。容器“已启动”不是服务“已就绪”。

Java 重启中的运行任务标为 `PROCESS_INTERRUPTED`，用户显式重试，不自动重新计费生成。单实例 PostgreSQL advisory lock 不等于跨主机故障接管；数据库异常后应检查并重启唯一 Java 实例，不另起第二套 workers。

## 当前架构备份和恢复

```powershell
docker compose stop frontend backend agent
python backend/scripts/backup_java_deployment.py --output E:\backups\trip-java-20260915
docker compose up -d --wait backend agent frontend
python backend/scripts/restore_java_backup.py --backup E:\backups\trip-java-20260915 --output E:\backups\trip-java-restored
```

备份包含业务库、上传、Chroma、账本、配置和镜像标识，包含敏感信息，放在仓库外并限制访问。恢复工具校验哈希、拒绝不安全归档成员，在独立数据库和目录启动禁用 workers 的克隆并保留结果供核对；不会覆盖日常配置或删除现有数据。

正式回切必须安排维护窗口，先备份当前数据，再核对数据库、上传、Chroma 和账本的一致性。源数据库凭据改变时，单独调整恢复用配置副本，不改原备份。跨机器恢复需先准备对应镜像和私有网络。

## 迁移历史与旧版本回滚

原始数据为 9 用户、2 偏好、25 行程、4 任务、21 历史同步作业、2 资料、5 资料作业。18 行程原属已不存在的用户 1，经用户确认原样保留；其余 7 条按原所有者验收。没有重分配孤立记录或补造账号。

- 迁移前最终备份：`E:\project\trip-planner-backups\final-pre-java-20260915-1310`。
- 迁移后备份：`E:\project\trip-planner-backups\post-java-20260915-1331`。
- 恢复目录：`E:\project\trip-planner-backups\post-java-restored-20260915-1333`。
- 清理前源码：Git 提交 `6c72a24`；仓库外归档 `E:\project\trip-planner-backups\pre-cleanup-source-20260915.zip`。

旧迁移工具、旧 Compose 和旧业务源码仅作为历史资料，可从该提交或归档取回到**独立目录**。用户已决定不再回退 Python 架构，5 个旧架构回滚镜像已删除，不再提供现成旧镜像恢复路径。后续恢复以当前 Java＋Agent 的成套数据库、上传文件、索引和配置备份为准；绝不能让旧代码直接连接新版 `trip_java`。

## 临时验证环境

`docker-compose.validation.yml` / `docker-compose.migration.yml` 仅用于离线夹具验证；`trip-validation` 不作为第二套日常服务常驻。CI 创建后销毁；本地验证后按项目名 `down`，不执行全局 Docker prune。

容器或镜像删除不代表删除数据：本次整理保留所有持久卷、宿主数据目录和备份；旧 Python 回滚镜像已另经用户确认删除。清理恢复克隆时只删除明确核对的克隆容器，不能按宽泛前缀误删日常四服务，更不能操作其他项目。

## 能力与测试归属

| 能力 | 当前实现 | 验证入口 |
|---|---|---|
| 登录、注销、偏好、账号隔离 | Java UsersController / Security | Java 测试、`java_api_contract_smoke.py` |
| 幂等、额度、任务、取消、超时 | Java TaskService + Agent 执行协议 | Java PostgreSQL 测试、HTTP 场景、重启／断流脚本 |
| 最终约束、预算、草稿分类 | Java PlanRules / TrustedCandidates | Java 原冻结场景、20 个 HTTP 业务场景；Python 纯函数保留离线对照 |
| 历史、编辑、改排、草稿应用 | Java HistoryController | 版本／所有者测试、Playwright |
| 原文件、审核、发布 | Java KnowledgeService + Agent extraction | `java_knowledge_smoke.py`、Agent 解析故障测试 |
| 向量同步与重建 | Java outbox + Agent indexing/rebuild | 稳定 ID、版本、墓碑、Java 快照冲突、检索回查 |
| 传统地图、图片、最终路线 | Java 高德 REST | Java 网关测试、缓存／错误映射、Agent 停机演练 |
| 模型、Agent 候选、检索与解析 | Python Agent + 高德 MCP | pytest、MCP 协议、RAG／视觉故障用例 |
| 数据恢复与通知 | 当前 Java 运维脚本 | `java_recovery_drill.py`、`notification_smoke.py` |

清理前的 255 项 Python 测试包含已退役业务代码测试，不能用它与清理后的 Agent 测试数直接比较覆盖率。公开业务合同转移到 Java 和 HTTP 验收，不保留可运行的旧业务后端来维持测试计数。

## 付费能力与单机边界

本轮真实验收已结束，账本保留；整理阶段仅离线测试，不自动追加模型、高德或 embedding 调用。资料解析、索引重建和真实生成可能计费，执行前需另行确认预算。取消、超时或断流不保证已发出请求不计费。

日常四容器不附带额外 Prometheus／Alertmanager 服务，告警配置和本地验收脚本仍保留。公开入口不暴露指标和内部服务；生产通知接收端、备份调度、容量规划及高可用需自行部署，不能把本机验证作为生产 SLA。
