# 单机运行手册

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

2026-11-07，本机部署保留原挂载，使用 Java 业务后端和 Python Agent。Agent 与前端的行程事实修复源码检查点为 `909fbc4`，见[本轮验收](../evidence/itinerary-facts-20261107/report.md)；Java、数据库及监控沿用已验收的生产代码。日常七服务验收后均停止。保留两套已验证的手工恢复点（升级前及 `f74d7fa` 数据与镜像快照）；定时备份任务保持 Disabled，详见[备份与恢复](java-migration.md#备份和恢复)。

本机维护管理员已完成正常注册、Bootstrap、MFA 确认及密码＋TOTP 重新登录。私有凭据保存在 `.codex-optimization/daily-window/runtime/maintenance-admin.private.json`，仅在本机读取。服务启动后通过正常登录进入 `/admin/operations`、`/knowledge/admin` 等管理页面；管理员权限继续受 MFA、账号状态和审计约束。

## 配置与数据

下列环境文件均相对于 `.codex-optimization/daily-window/runtime/`；根目录四服务模板首次生成的对应文件位于 `deploy/runtime/`，两套路径不能混用。

- Java 配置：`business.env`；仅它持有运行时业务数据库和用户 JWT 配置。
- Java 地图配置：`business.env` 中的 `AMAP_REST_API_KEY` 与有界缓存 TTL；公开天气／路线、手工组装及用户主动复核使用此通道。公开 POI 搜索／详情经 Java 转发到 Agent MCP，图片由 Java 同源代理读取 Agent 返回的可信地址。
- Agent 配置：`agent.env`；模型、`AMAP_API_KEY`、embedding、视觉、内部密钥与固定 Java 地址，不含数据库凭据。首次配置脚本将源文件中的 `AMAP_MCP_API_KEY` 转入运行配置的 `AMAP_API_KEY`。
- 日常模型：文本／视觉为官方 `gpt-6.1-sol`，Embedding 为官方 `text-embedding-3-large`、3072维，三角色模型访问检查均为 HTTP200。全量 RAG 已重建并启用，迁移详情见[模型与索引手册](openai-migration.md#本机全量迁移结果2026-11-07)。
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

需要日常使用时，以下命令启动已有七个容器；暂停时停止全部七服务，保留配置、镜像、数据卷与绑定目录。仅在需要运行服务时执行 `start`：

```powershell
docker compose -p langchain-trip-planner -f $dailyCompose start --wait postgres backend
docker compose -p langchain-trip-planner -f $dailyCompose start --wait agent frontend prometheus alertmanager alert-receiver
docker compose -p langchain-trip-planner -f $dailyCompose stop
```

修改运行环境文件后，`start` / `restart` 不会重新载入新配置；确认需要更新时，在同一项目和 Compose 文件下仅重建受影响服务，例如 `docker compose -p langchain-trip-planner -f $dailyCompose up -d --no-build --force-recreate agent`。应用升级须先准备并验证新镜像，再更新原配置中的相应镜像引用；保留现有环境文件、挂载、端口与运行限制。不要运行首次配置生成脚本、改用根目录 Compose，或使用 `down -v` 排查问题。

Agent 启动时向 Java 核对 `DATASET_ID`，因此先等待数据库和 Java 就绪，再启动其余服务。首次安装与公开部署的 Compose 已声明 `agent → backend(service_healthy)`；维护既有停止的容器时仍按上面的两阶段命令启动，避免在 Java 尚未就绪时触发 Agent 的连接失败退出。

## 健康与故障检查

Java 存活检查不调用模型，就绪检查验证数据库、迁移和本地必要配置；前端依赖 Java 健康。Agent 就绪检查验证 AI 服务本身。停止 Agent 的演练中，检查登录、收藏、手工行程、Java 路线、历史、分享和导出继续服务；经 Agent 的 POI 查询与智能规划返回 `AGENT_UNAVAILABLE`。启动后检查 readiness，再执行对应业务流程。

Java 重启中的运行任务标为 `PROCESS_INTERRUPTED`，用户显式重试，不自动重新计费生成。当前采用单实例 PostgreSQL advisory lock；数据库异常后应检查并重启唯一 Java 实例，不另起第二套 workers。

内部 Prometheus 从 Java 的 `/actuator/prometheus` 抓取 Actuator/Micrometer 指标；Nginx 对公网入口显式拒绝 `/actuator/*`、`/metrics` 和 `/internal/*`。`X-Request-ID` 会进入 Java MDC、任务记录、审计事件和 Java→Agent 请求头，排障时应以该字段关联日志，不把用户 ID、任务 ID或提示词作为指标标签。

## 首次安装后的四服务维护

### 常用操作与配置更新

以下命令用于根目录 `docker-compose.yml` 的四核心服务环境，仍在项目根目录执行。本机现有七服务环境使用[显式指定私有 Compose 文件的命令](single-host.md#现有七服务部署操作)，配置修改位置也沿用其 `runtime/` 目录。

```powershell
# 查看启动问题与最近日志
docker compose logs --tail 100 backend agent frontend

# 暂停服务，保留数据
docker compose stop

# 再次启动
docker compose up -d --wait
```

上文首次生成配置后，根目录 Compose 实际读取的是 `deploy/runtime/*.env`；本机七服务读取 `.codex-optimization/daily-window/runtime/*.env`。后续修改 `backend/.env` 不会自动同步到运行配置，下表路径仅对应根目录四服务模板：

| 修改内容 | 修改位置 | 生效命令 |
|---|---|---|
| 文本模型、RAG、视觉配置 | `deploy/runtime/agent.env` | `docker compose up -d --force-recreate agent` |
| Java 服务端高德 Key | `deploy/runtime/business.env` 的 `AMAP_REST_API_KEY` | `docker compose up -d --force-recreate backend` |
| Agent 高德 Key | `deploy/runtime/agent.env` 的 `AMAP_API_KEY`（脚本从 `AMAP_MCP_API_KEY` 转入） | `docker compose up -d --force-recreate agent` |
| 前端地图 Key | `frontend/.env` 的 `VITE_AMAP_WEB_JS_KEY` | `docker compose up -d --build frontend`，完成后刷新浏览器 |

`docker compose restart` 不会重新载入修改后的环境文件，应使用表中的重建容器命令。不要使用 `down -v` 或删除 `backend/data` 来排查问题；数据库卷、上传文件、索引及私有配置需要成套备份，详见[备份与恢复前提](java-migration.md#备份和恢复)。

### 常见问题

- **页面打不开或启动失败？** 先确认 Docker Desktop、所选部署的私有配置和容器状态；本机七服务目前按维护要求停止，需要使用时按[运行手册](single-host.md#现有七服务部署操作)启动已有容器。根目录首次部署使用 `deploy/runtime`，查看 `docker compose ps` 和日志；端口冲突可修改 `APP_PORT`，首次构建失败时检查镜像和依赖下载网络。
- **能登录，但无法生成行程？** 检查 Agent 的运行配置中模型名称、API 地址、Key 是否匹配，以及高德 MCP 是否可访问。`AGENT_UNAVAILABLE` 表示 Agent 不可用；登录和历史查看仍可使用。
- **地图不显示或景点没图片？** 核对前端 JS API Key 的类型与高德控制台的安全配置、域名限制，修改 Key 后重新构建前端。某些景点没有可信图片时会显示占位提示。
- **为什么有资料缺口或降级结果？** 可能是路线、餐饮、天气或知识资料不足；未启用 RAG 时也可能缺少攻略上下文。请查看具体提示，调整或补充核验，不要把草稿当作完整方案。
- **中断后恢复是否重新扣费？** 兼容且未过期的阶段可以复用；已发送但尚未持久化响应的调用按不确定处理。新调用仍先预留预算，失败不退计数，实际费用以供应商账单为准。重复点击恢复使用同一幂等键不会创建多份执行。
- **30 天是否都能算出最优结果？** 输入仍支持 1–30 日，但求解器有候选、路线查询及总时间上限。达到上限会保留有效阶段并说明原因；返回的是有界搜索结果，长行程性能需按实际输入检查。
- **需要资料审核管理员？** 先注册账号，再在所选部署的 `business.env` 添加 `BOOTSTRAP_ADMIN_USERNAME=已注册的用户名`，使用同一 Compose 文件重建 `backend`。根目录四服务环境使用 `deploy/runtime/business.env`；本机七服务路径与命令见[运行手册](single-host.md#现有七服务部署操作)。
- **可以直接部署到公网吗？** 本指南面向本机单实例使用；公网部署还需配置 HTTPS、访问入口和备份策略。当前不提供多实例高可用部署。

迁移及恢复前提见[迁移与升级手册](java-migration.md)。

## 宿主容量检查

宿主容量可单独只读检查，无需启动日常服务：

```powershell
python tools/single_host_inspection.py --path E:/project/langchain-trip-planner --path E:/Docker --project langchain-trip-planner --output .codex-optimization/host-inspection.json
```

路径须替换为本机实际项目／Docker数据位置；可重复传入 `--path`，也可指定现有VHDX文件以同时记录文件大小。默认剩余空间低于10%返回非零，路径不存在或Docker读取失败也明确失败。该检查观测宿主文件系统；Linux卷空间、数据库WAL、应用健康和外部通知分别通过对应监控检查。命令只读，不安装任务、启动服务或启用备份。
