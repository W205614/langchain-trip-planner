# 单机可靠性实现与运行手册

本轮保留 FastAPI、LangGraph、PostgreSQL、本地 Chroma 与 Compose。模型负责候选编排，后端控制身份、任务状态、POI 事实与保存事务。`MultiAgentTripPlanner` 是兼容类名，当前仍是单模型工作流。

## 运行契约

- `POST /api/trip/tasks` 接收原旅行请求，返回 202 和 `data.id`。携带稳定的 `Idempotency-Key`；同用户、同键、同内容返回同一任务，不同内容返回 409。
- `GET /api/trip/tasks/{id}` 与 `GET /api/trip/tasks/{id}/events` 均验证归属，跨用户返回 404。SSE 恢复时发送当前状态，不重放所有进度。旧 `/plan`、`/plan/stream` 共用该执行层。
- 前端把请求、幂等键、任务 ID 保存在 sessionStorage；同一标签页刷新可恢复。“我的任务”提供按用户分页、取消、显式重试和结果找回。主动退出会撤销该账号所有旧登录凭证并清除本地缓存。
- 等待和执行共享默认 300 秒截止时间；单日异步模型流与纠错共用单日预算。断线只停止订阅；重启把原 running 标记 `PROCESS_INTERRUPTED`，不自动重付模型费用；queued 继续调度。
- 成功状态、历史记录、质量报告与 RAG outbox 在同一事务提交。提交失败不能显示“已保存”。调用线程拥有独立数据库会话；SSE 鉴权查询后立即归还连接。
- 历史 PUT 与单日改排要求 `If-Match: <version>`。前端改排通过 revise-task 提交持久化任务，执行与提交分别校验版本；冲突以 VERSION_CONFLICT 终止。旧同步 revise-day 使用同一任务执行层，冲突返回409。
- `quality` 与预算的不确定项持久化；零金额保持零，缺少价格不等于免费。门票、餐饮、酒店属于估算，交通金额未知；候选 POI 并不核实开放时间、预约条件或价格。

## 数据与权限

`POST /api/rag/rebuild` 仅管理员可用，2 次/小时，重建互斥且记录操作者及结果。嵌入探测异常或维度不符时保留旧集合并降级。显式重建从静态 Markdown、published 资料 source_text、全部历史主表生成新集合，检查数量与检索后原子替换 `active-index.json`。旧集合不自动删除。动态高德缓存尽量从可读旧集合保留，损坏缓存可重新查询生成。

审核、拒绝与删除增加版本条件；解析任务提交前重新验证状态和版本。拒绝/删除后检索先查 SQL 有效性，向量尚未清理也不可见。删除是墓碑加异步文件/向量清理。资料拒绝保留原文件用于再次审核，不等于物理删除。

RAG 不可用时历史同步及知识发布进入 waiting，不消耗失败次数；恢复后补偿。其他失败按退避重试，达到上限进入 failed。管理员使用 `GET /api/rag/jobs` 检查，`POST /api/rag/jobs/{history|knowledge}/{id}/replay` 重放有效任务。

当前通过进程内锁与数据目录 `service.lock` 限制单 API 进程。不要使用 `uvicorn --workers 2`，不要给同一数据库配置两个独立数据目录的 API 实例。限流也是单进程内存状态。重建期间索引锁会暂停其他索引操作；应在维护窗口执行。

## 本地更新和回滚

镜像是容器模板。`docker compose up -d --build` 重建已有服务容器并保留配置的数据挂载；在旧容器内手工 pip install 会产生不可复现状态。临时 `trip-validation` 项目只用于隔离验证，不能拿它替换真实数据库。

原本机 Compose 继续使用 `docker-compose.yml`、`backend/.env` 和可选 `.env.docker`，保留 `/app/data` 绑定。先停止原后端，再执行：

```powershell
python backend/scripts/backup_local_deployment.py --output E:/project/trip-planner-backups/<新的备份目录>
docker compose -p langchain-trip-planner build
docker compose -p langchain-trip-planner up -d --wait
```

备份脚本只支持当前本机 PostgreSQL 配置，使用 Docker 的 PostgreSQL 17 客户端。备份含私有数据，放在仓库之外；输出 database.dump、application-data.tar.gz、SHA256 清单和原镜像 ID。恢复时先校验 SHA256，再恢复到独立空数据库和目录验证；不要把演练指向日常数据库。模型密钥不写入备份清单。

旧镜像与旧数据备份必须一起保留。若迁移后回滚，先停服务，以备份恢复到独立库、目录并切回原镜像及对应配置；不要把旧版 ORM 直接指向不兼容的新 schema。已有数据库使用 Alembic，禁止以 `create_all` 代替生产迁移。SQLite 的补列兜底只用于开发。

## 独立生产配置

`docker-compose.production.yml` 提供 PostgreSQL 独立卷、应用数据卷、生产密钥校验和只绑定本机端口的前端/监控。它是可部署配置，尚未购买云资源或接入真实生产流量。准备 `POSTGRES_PASSWORD` 与 `JWT_SECRET_KEY` 环境变量后，用该文件显式启动；建议密码使用 URL 安全字符，数据库密码含特殊字符时需正确 URL 编码。不要与原本机 Compose 共用项目名后直接启动，以免意外切换数据库。

`DATA_DIR`、`CHROMA_DIR`、`UPLOAD_DIR`、`LOG_DIR` 均可配置；备份必须覆盖实际路径。API 的 `/metrics` 仅内网访问，Nginx 拒绝公网 `/metrics`。Prometheus 展示任务积压、超时/中断、同步状态及索引重建耗时；`deploy/alerts.yml` 配置服务不可用和积压规则。隔离验证栈已接入 Alertmanager 与本地通知接收器，生产配置尚未配置短信、邮件或外部通知渠道。

## 隔离验收

Python 3.11 使用 `requirements.lock` 固定依赖版本（通过 requirements.txt 引用）；修改 requirements.in 后重新解析、验证并更新 lock。前端与 CI 严格 `npm ci`。锁定版本不等于锁定镜像 digest 或完成供应链审计。

```powershell
docker compose -p langchain-trip-planner build
docker compose -p trip-validation -f docker-compose.validation.yml up -d --wait postgres backend frontend
docker compose -p trip-validation -f docker-compose.validation.yml exec -T postgres createdb -U trip trip_tests
docker compose -p trip-validation -f docker-compose.validation.yml run --rm tests
docker compose -p trip-validation -f docker-compose.validation.yml exec -T backend python scripts/smoke_http.py --base-url http://frontend
docker compose -p trip-validation -f docker-compose.validation.yml exec -T backend python scripts/business_benchmark.py --base-url http://frontend --output /tmp/business.json
```

测试数据库 trip_tests 已存在时无需再次 createdb。在 frontend 目录执行 `npm ci`、`npx playwright install chromium`、`npx playwright test`。随后从项目根目录执行：

```powershell
python backend/scripts/recovery_drill.py --output docs/evidence/recovery-offline.json
python backend/scripts/lifecycle_smoke.py
docker compose -p trip-validation -f docker-compose.validation.yml down -v
```

最后一条仅清理本轮隔离测试项目，绝不可对日常项目执行 `down -v`。恢复脚本固定验证项目，创建随机独立数据库，恢复七张表并比对内容摘要，在临时目录验证上传文件及重新嵌入历史/已发布资料，结束删除演练数据库。故障测试包含损坏备份的校验拒绝。

`validation_server.py` 仅在明确的 validation 环境和 fixture 开关同时开启时替换模型与地图服务。它禁用限流以运行批量业务场景，因此这些结果不证明真实上游质量、线上吞吐或限流效果。正常镜像启动入口不使用此脚本。

## 效果证据与剩余边界

20 个场景经 HTTP、JWT、任务、路线校验和 PostgreSQL 保存，分别记录规则通过、降级、持久化、时延与 fixture token 用量，见 [业务报告](evidence/business-offline.json)；[恢复报告](evidence/recovery-offline.json) 是独立演练证据。规则通过不等于用户满意，人工满意度保留为空。

用户随后授权实际功能验收，已完成一次真实模型/高德的一日行程及页面操作，见 [真实功能验收](evidence/live-functional-20260910.json)。18 项 HTTP 检查通过，单次模型调用无生成兜底，但公交路线存在可见降级。此样本不属于批量效果评测。实际页面检查发现并修复了质量提示挤压分栏布局的问题，并补充横向溢出断言。

真实模型评测默认关闭。独立评测进程设置 `LIVE_EVAL_ENABLED=true`、调用上限、美元预算、输入/输出单价，再以管理员 EVAL_TOKEN 运行 benchmark 的 `--live` 模式。预留费用按提示字节与最大输出保守估计，计数是进程级，重启会重置；供应商账单与价格仍需人工核对。不要在普通 CI 中放真实密钥。本轮未运行批量付费效果评测；随后授权的一次真实功能验收已单独记录。

同步高德与本地索引工作不能被 Python 强杀；网络有超时，任务截止后即使旧线程较晚返回，也不能提交成功。异步单日模型流已测试超时关闭，但这不代表任意第三方 SDK 都具备硬实时终止保证。下一阶段重点是更真实的困难案例人工评分、任务状态列表、备份保留策略和告警通知接入，而非微服务拆分。
