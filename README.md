# 智能旅行助手：Java 业务后端 + Python Agent

[![CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml)

Vue 提交需求，Spring Boot 管理账号、任务与数据一致性，Python LangGraph 获取可信地点并生成行程。结果分为完整、降级和未完成草稿，不能核实的路线、预算和预约信息会明确标注。这是可验证的**单机、单 Java 实例、单 Python 实例**项目，不是高可用平台，不承诺生产 SLA 或旅行事实准确率。

## 架构与职责

```text
浏览器 Vue / Nginx :8080
          │ /api/*、JWT、公开 SSE
          ▼
Java Spring Boot :9000 ─── PostgreSQL（业务唯一事实源）
          │                   账号/偏好/行程/任务/资料/outbox
          │ /internal/v1，独立服务密钥
          ▼
Python Agent :9000 ─── 高德 MCP 或 REST / 模型 / embedding
          │
          └── Chroma + 运行期防重/调用预算账本
          ▲
          └── 回查 Java：候选可见性、原文件、重建快照
```

| 层 | 职责 | 入口 |
|---|---|---|
| Java 21 / Spring Boot 4.0.3 | Security/JWT、偏好、任务/取消、版本冲突、最终规则、资料审核、outbox、限流/指标 | `business-backend/src/main/java/com/tripplanner/` |
| MyBatis Starter 4.0.0 / Flyway / PostgreSQL 17.6 | 条件更新、唯一约束、短事务、业务数据迁移；不使用 Redis/消息队列 | `business-backend/src/main/resources/db/migration/` |
| Python 3.11 / LangChain / LangGraph | 候选查询、逐日生成/局部改排、MCP/REST、RAG、图文解析与向量写入 | `backend/app/agent_api/main.py` |
| Vue 3 / TypeScript | SSE/任务恢复、地图、编辑、PNG/PDF/离线 HTML 导出、知识复核 | `frontend/src/` |

依赖兼容依据：[MyBatis 官方矩阵](https://mybatis.org/spring-boot-starter/mybatis-spring-boot-autoconfigure/)。Python 锁定在 `backend/requirements.lock`，图像解码使用 [Pillow 12.3.0](https://pypi.org/project/pillow/12.3.0/)；Maven Wrapper 为 Maven 3.9.9。旧 Python ORM、业务路由与 Alembic 留作迁移参考和回归基线，**当前 Agent 不挂载或启动它们**，运行时不接收业务数据库/JWT 凭据。

## 可靠性与可信边界

- 任务状态保留 `queued/running/succeeded/needs_attention/failed/cancelled`。幂等键绑定请求指纹；同键不同请求冲突。Java 有界线程池领取任务，最终状态、行程与 outbox 同事务提交，外部调用不占用业务事务。
- 内部执行携带协议版本、任务/执行/请求 ID、用户、绝对截止时间；改排带原快照和版本。Python 返回进度/用量/结果/错误及心跳；Java 对浏览器保留 `progress/complete/error` SSE。
- Python 同进程执行 ID 不重复调用模型，登记保留到进程退出，上限 10,000 个后拒绝新执行。断流/重启不自动重新生成；显式重试创建新任务。Java 重启将原运行任务标记 `PROCESS_INTERRUPTED`，继续未过期排队任务。
- 取消/超时尝试停止后续调用，已发出请求可能计费；迟到结果同时检查状态、执行 ID、截止时间和原版本。
- Java 最终裁决可信 POI、名称别名、必去/排除、跨日去重、时间/步行限制、真实路线、预算及草稿分类。缺失票价不等于免费，未知路线不等于零距离，手工编辑带未外部核实标记。
- 资料遵循“投稿 → 允许解析 → 人工复核 → 版本绑定发布”。解析成功不会自动公开。Java 管理原文件，内部读取只接受资料 ID/版本，不接受路径。
- outbox 串行消费有效版本；Python 稳定向量 ID、持久版本水位和删除墓碑阻止旧作业覆盖。失败退避、可见错误和重放；索引发布重试不重新解析图片。
- 检索回查 Java 所有者、版本、发布/草稿状态；校验不可用排除受影响资料并降级。重建由一致性快照生成独立集合，核对业务/索引变更序号后切换，冲突保留旧集合。
- 高德由 LangGraph 数据节点显式选择，`AMAP_TRANSPORT=mcp/rest` 不静默切换，不是模型自主执行任意工具。酒店、天气、静态攻略都有时效边界，预约与票价以官方为准。
- 景点图片按真实 POI ID 优先尝试最多 3 张；无图时，仅对名称前缀匹配、同城、坐标相距不超过 3 公里且唯一的景区候选使用带“景区参考图”标注的图片。不把搜索首图当作具体点位实拍。限制下载大小/像素、校验真实图片格式和逐跳公网地址；图片请求传播 24 秒截止时间，成功缓存 1 小时、失败短缓存 30 秒，占位图不进入浏览器长缓存。没有可信图源仍明确提示暂不可用，不保证所有 POI 有实拍。

## 启动与数据迁移

需要 Docker Compose。访问 **http://localhost:8080**；Java、Agent、PostgreSQL 不发布宿主机端口，Nginx 不代理 `/internal/*` 或 `/metrics`。

1. 按 `backend/.env.example` 配置能力凭据；已有用户必须保留原 `JWT_SECRET_KEY`。前端按 `frontend/.env.example` 配置地图 JS Key。
2. 在装有 `python-dotenv` 的环境执行以下命令，生成不入 Git 的 `deploy/runtime/{postgres,business,agent}.env` 并初始化空 Flyway 库：

   ```powershell
   python backend/scripts/prepare_java_deployment.py
   docker compose build
   docker compose up -d --wait postgres backend
   ```

3. 默认 `WORKERS_ENABLED=false`。已有数据必须先备份、向**独立空目标库**导入并核对，见 [迁移/备份/恢复手册](docs/operations/java-migration.md)。全新空部署或核对完成后，把 `deploy/runtime/business.env` 的 `WORKERS_ENABLED` 改为 `true`：

   ```powershell
   docker compose up -d --wait backend agent frontend
   ```

日常启动 `./start-local.ps1`（`-Build` 重建）。生产单机使用 `docker-compose.production.yml` 与独立持久卷，需自行配置私有环境、TLS 和备份，不自动具备互联网生产保障。

Java 持有数据库/JWT 和任务队列/用户额度配置；Agent 仅持有能力凭据和独立服务密钥。登录兼容 bcrypt 72 UTF-8 字节及 JWT `sub/exp/ver`，注销递增令牌版本。Java 存活 `/healthz`，就绪 `/readyz` 验证数据库/迁移；Agent 就绪检查本地存储，单独报告 RAG 状态，健康检查不调用模型。

保留旧业务 ID、哈希、JSON、日期、所有者、版本和作业信息，修复序列并逐表核对。当前 18 条历史行程原属已不存在的用户 ID 1，经确认原样保留、不重新分配。Chroma 先复制验证，不兼容时保留原索引并显式降级。

## 验证

证据与未完成项见 [本轮验收报告](docs/evidence/java-migration/README.md)。必须核对准确提交对应的远程 CI，不能凭徽章认定当前工作区已经交付。

```powershell
# 在 business-backend 目录；设置 TEST_DATABASE_URL 后执行真实 PostgreSQL 集成测试
./mvnw.cmd test
# 以下从项目根目录执行，只用固定隔离项目
docker compose -p trip-validation -f docker-compose.validation.yml up -d --build --wait postgres backend agent frontend
docker compose -p trip-validation -f docker-compose.validation.yml exec -T postgres createdb -U trip trip_tests
docker compose -p trip-validation -f docker-compose.validation.yml run --rm tests
# 在 frontend 目录
npx playwright test
```

`trip_tests` 只在首次验证创建。Java 测试数据库与凭据见 CI。HTTP、资料复核、恢复、断流/取消、重启和告警脚本在 `backend/scripts/`；固定隔离脚本不能改指向日常数据库。共享协议及样例在 `contracts/internal-v1/`。

真实小样本使用 `java_live_acceptance.py`，私有状态必须位于仓库外；新验收账号与已有管理员短期签名测试令牌不等于原账号密码登录证据。资料解析与发布为分开的命令，中间必须核对原文。

本轮独立上限为文本 12、视觉 1、embedding 50、高德 100；失败也计数、SDK 自动重试关闭。`backend/data/agent-runtime/acceptance-budget.sqlite3` 持久预发送计数，崩溃可能保守多计但重启不清零。某类别耗尽只阻止该类请求，**不承诺精确金额封顶**。本轮以 4/1/14/100 次结束；用户确认恢复日常使用后，将私有 Agent 配置的 `ACCEPTANCE_BUDGET_FILE` 置空并重启，原账本保留。验收脚本在预算未启用时拒绝运行；后续用户日常操作不属于这轮自动验收，不再声称受本轮额度保护。

历史架构文档和旧测试数字见 [README.python-legacy.md](README.python-legacy.md)，不与新架构测试相加。旧 `backup-postgres.ps1`、`restore-postgres-backup.ps1`、`backup_local_deployment.py`、`recovery_drill.py` 仅是旧栈参考，不用于 Java 数据库。

![旅行规划首页](docs/screenshots/home.png)
