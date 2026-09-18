# 🧭 AI 旅行规划 Agent：Java 业务后端 + Python Agent

[![CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml)

基于 **Spring Boot、LangGraph 和 Vue 3** 的国内旅行规划应用。输入城市、日期与偏好，获取可查看、编辑和导出的逐日行程；规划任务可恢复，资料经过人工复核后才能进入公开检索。

**业务状态交给 Java，旅游生成与检索交给 Python Agent。** PostgreSQL 是业务唯一事实源，Python Agent 不包含业务 ORM、用户鉴权或公开业务路由；但景点选择、路线查询、约束修复和生成结果质量分类由 Agent 一次完成。项目定位是可验证的单机应用，不是高可用平台，也不把静态攻略或模型输出当作实时旅行事实。

## ✨ 功能特点

- 🤖 **多日旅行规划**：LangGraph 并行获取景点、天气和酒店，再按日生成；使用经过校验的真实 POI，不用虚构地点填满行程。
- 🧭 **Agent 统一入口**：首页创建新行程；历史记录内直接进行攻略问答和单日改排；景点发现由 Agent 调用高德 MCP，不再提供独立助手、收藏转手工行程两套入口。
- ⚡ **任务与流式进度**：Java 持久化排队、进度和结果；支持 SSE、刷新恢复、幂等提交、取消、超时与中断状态；需要操作的任务集中显示在“我的行程”。
- 🧭 **Agent 质量分类**：Agent 使用高德 MCP 完成可信 POI 选择、路线查询、步行／时间约束修复和预算计算；路线暂不可用时保存可调整的降级行程，不再由 Java 二次规划。
- 🗺️ **地图与图片**：高德地图展示路线；图片按 POI ID 获取，无独立实拍时仅使用经过校验且明确标注的景区参考图，无可信来源则显示占位提示。
- ✏️ **行程编辑**：添加、删除、调整景点，局部改排；版本冲突不会静默覆盖已有修改，草稿需要明确确认后应用。
- 🧾 **版本与审计**：创建、编辑、核验、改排、确认和恢复都会生成不可变版本；关键业务动作保存脱敏审计事件，删除行程会同步撤销分享并解除会话关联。
- 🧠 **RAG 检索**：Chroma 管理城市攻略和个人历史向量；Java 回查资料所有者、版本、发布状态和草稿状态，不满足可见性要求的资料不进入上下文。
- 📄 **图文资料审核**：PDF／图片投稿、解析、人工复核、版本绑定发布；解析成功不等于自动公开。
- 📚 **历史与偏好**：账号登录、历史分页与筛选、用户主动保存和删除偏好；不同账号的数据隔离。
- 🔎 **资料研究**：公开城市资料检索返回来源、页码和来源等级，不读取他人的私人历史。
- 📤 **三种导出**：PNG、PDF 和离线 HTML；导出包含全部日期，离线 HTML 可展开／收起。
- 🛡️ **可靠性验证**：事务、幂等竞争、版本冲突、断流、取消后迟到、恢复、发布和告警都有对应测试入口。

## 📸 界面预览

以下为功能界面示例，具体布局以当前页面为准。

![旅行需求表单](docs/screenshots/home.png)

![每日行程与地图](docs/screenshots/result.png)

![行程详情](docs/screenshots/result1.png)

![我的行程管理](docs/screenshots/history.png)

## 🏗️ 技术栈

| 部分 | 技术 | 职责 |
|---|---|---|
| Java 业务后端 | Java 21、Spring Boot 4.0.3、Spring Security、MyBatis Starter 4.0.0、Flyway、Actuator、Micrometer | 用户、业务 API、任务、协议校验、版本审计、资料审核、事务与 outbox |
| Python Agent | Python 3.11、FastAPI、LangChain、LangGraph | 旅游生成、高德 MCP 景点／天气／酒店／路线、约束修复、质量分类、检索、解析及向量写入 |
| 存储 | PostgreSQL 17.6、Chroma、本地持久目录 | 业务数据、向量索引、上传文件及操作账本 |
| 前端 | Vue 3、TypeScript、Vite、Ant Design Vue、高德 JS API | 交互、SSE、地图、编辑与导出 |
| 部署与验证 | Docker Compose、Nginx、JUnit、pytest、Playwright、GitHub Actions | 单机部署、自动化回归与恢复演练 |

Python 依赖锁定在 `backend/requirements.lock`，Maven Wrapper 版本为 3.9.9。模型名称和端点由配置决定；兼容协议不代表每个供应商都支持相同参数或返回格式。

## 🏛️ 架构分层

```text
浏览器 / Vue
    │ 同源公开 API、SSE
    ▼
Nginx :8080 → Java 业务后端
                 ├── PostgreSQL：用户、任务、行程、资料、outbox
                 ├── 高德 REST：公开地图能力、用户主动复核与安全图片代理
                 ├── 安全图片代理：下载并校验 Agent/MCP 返回的图片
                 ├── 上传目录：原文件与资料版本
                 └── /internal/v1 → Python Agent
                                      ├── LangGraph → 文本模型
                                      ├── 高德 MCP：景点、天气、酒店、路线与图片
                                      ├── PDF / 图片解析 → 视觉模型
                                      └── RAG / embedding → Chroma

Python → Java 内部回查：资料可见性、原文件、重建快照与变更序号
```

- **Java 负责业务状态**：鉴权、历史版本、任务生命周期、幂等、协议与可信候选一致性校验、最终状态和事务提交。
- **Python 负责完整旅游生成**：返回结构化计划和可信候选，并在 Agent 内完成路线查询、约束修复、预算与质量分类；不写业务表，也不处理用户权限。
- **只规划一次**：Java 不再重查 POI、替换景点或重新计算路线，只验证执行编号、请求一致性、可信候选和质量协议后持久化。
- **内部接口不对公网开放**：独立服务密钥，固定地址；Nginx 不代理 `/internal/*`、`/metrics` 或 `/actuator/*`，Agent 不发布宿主机端口。
- **四容器属于一个项目组**：`langchain-trip-planner` 下保留 `frontend / backend / agent / postgres`，不把数据库与业务进程强塞进同一个容器。

## 📁 项目结构

```text
langchain-trip-planner/
├── business-backend/              # 当前 Java 业务后端
│   ├── src/main/java/com/tripplanner/
│   │   ├── api/                   # 公开业务与内部回查接口
│   │   ├── domain/                # 任务、规则、审核、outbox
│   │   ├── persistence/           # MyBatis
│   │   └── security/              # 用户鉴权与令牌
│   └── src/main/resources/db/migration/ # Flyway
├── backend/                       # 当前 Python Agent，保留目录名以避免搬动数据
│   ├── app/agent_api/             # 唯一 HTTP 服务入口 /internal/v1
│   ├── app/agents/                # LangGraph 生成工作流
│   ├── app/services/              # 地图、图片、模型、RAG 和纯数据辅助函数
│   ├── app/evals/                 # 离线评测与冻结规则对照
│   ├── tests/                     # Agent 能力与故障测试
│   ├── scripts/                   # 当前部署、备份、恢复与接口验收
│   └── data/                      # 原文件、Chroma、操作账本；运行数据不提交
├── frontend/                      # Vue 页面与 Playwright
├── contracts/internal-v1/          # 共享 JSON Schema 和测试样例
├── deploy/                        # 配置模板、告警规则；runtime 私有配置不提交
├── docs/                          # 截图、运行手册与当前架构证据
└── docker-compose*.yml            # 日常、生产与临时隔离验证配置
```

已删除旧 Python 业务 API、SQLAlchemy 业务模型、Alembic、用户 JWT、业务调度和旧部署脚本。仍保留的纯规划辅助函数和冻结场景用于生成及规则对照，不代表 Python 有第二套在线业务后端。历史源码可从 Git 提交 `6c72a24` 恢复。

## 🚀 快速开始

### 前提条件

- Docker Desktop / Docker Engine 与 Docker Compose。
- 高德 Web 服务 Key，以及前端使用的高德 Web JS Key。
- 一个可用的文本模型端点；需要检索或图文解析时，再配置 embedding／视觉能力。
- 在宿主机执行配置和运维脚本时，需要 Python 与 `python-dotenv`；本地开发额外需要 Java 21、Node.js 20+。

### 1. 准备配置（仅首次部署）

```powershell
Copy-Item backend/.env.example backend/.env
Copy-Item frontend/.env.example frontend/.env
python -m pip install python-dotenv
```

编辑 `backend/.env`：Java 使用 `AMAP_REST_API_KEY`，Agent 使用 `AMAP_MCP_API_KEY`，按需要填写 `LLM_*`、`EMBEDDING_*`、`VISION_*`；编辑 `frontend/.env` 的 `VITE_AMAP_WEB_JS_KEY`。开发环境可让两个服务端 Key 取同一值，但部署文件和指标仍按通道分开。

```powershell
python backend/scripts/prepare_java_deployment.py
```

已有 `deploy/runtime` 的旧部署升级时使用 `python backend/scripts/prepare_java_deployment.py --upgrade-existing`；它保留数据库、JWT 和内部服务密钥，只补齐当前 Java REST / Python MCP 边界配置。

脚本生成 `deploy/runtime/postgres.env`、`business.env` 和 `agent.env`。新部署 JWT Key 留空时生成随机值；已有账户迁移必须保留原 Key。脚本拒绝覆盖已有目录，**已有日常环境不要重新生成配置**。

### 2. 构建并启动

```powershell
docker compose up -d --build --wait
docker compose ps
```

访问 **http://localhost:8080**。Flyway 自动初始化空业务库；不自动接管或 baseline 未知旧库。首次注册账号后可登录；审核管理员通过 `business.env` 的 `BOOTSTRAP_ADMIN_USERNAME` 显式指定已存在账号，重启 Java 生效。

已有 V1–V3 数据的环境升级到 V4 前，必须先停止 `frontend / backend / agent`，使用 `backup_java_deployment.py` 在仓库外生成完整备份，并执行[迁移手册](docs/operations/java-migration.md)中的孤儿数据与非法状态检查。V4 不会自动删除或改绑历史数据；检查不通过时 Flyway 会终止启动。完成数据所有权确认后，再运行 `prepare_java_deployment.py --upgrade-existing` 和上述 Compose 启动命令。`rag_sync_jobs.record_id` 特意不设置外键，以便删除行程后的索引墓碑继续完成。

### 3. 常用操作

```powershell
docker compose logs --tail 100 backend agent
docker compose restart backend
docker compose stop
docker compose up -d --wait
```

不要用 `down -v`、清理卷或删除 `backend/data` 来解决启动问题。修改模型或前端 Key 后，需要重启相应服务或重新构建前端。

## ⚙️ 配置说明

| 配置文件 | 关键项目 | 边界 |
|---|---|---|
| `deploy/runtime/business.env` | `JWT_SECRET_KEY`、`JDBC_DATABASE_URL`、`AMAP_REST_API_KEY`、`WORKERS_ENABLED`、`TRIP_*` | Java 独占业务数据库、用户鉴权和高德 REST |
| `deploy/runtime/agent.env` | `LLM_*`、`AMAP_MCP_API_KEY`、`EMBEDDING_*`、`VISION_*`、`RAG_ENABLED` | 只包含 AI 能力配置，不传业务库凭据 |
| 两个服务 | `INTERNAL_SERVICE_KEY`、`AGENT_URL` / `BUSINESS_URL` | 固定服务地址，独立内部密钥 |
| 前端构建配置 | `VITE_AMAP_WEB_JS_KEY` 等 | 仅前端地图配置；不放服务端模型 Key |

日常新配置默认启用 Java workers，预算验收模式默认关闭。受控验收通过持久 `ACCEPTANCE_BUDGET_FILE` 分类别限制实际调用；其开启、上限与停止规则必须单独确认。不要删除账本来重置额度。

**现有 Chroma 的模型和向量维度必须一致。** 更换 embedding 模型不能仅改环境变量；需备份后执行受控重建，失败保留旧集合，不直接删除索引。

## 📝 使用指南

1. 在首页选择城市、日期、偏好及约束，提交给 Agent 后查看持久任务进度。
2. 在“景点发现”中查看 Agent 通过高德 MCP 获取的真实 POI 与对应图片。
3. 先看结果分类与缺口：`needs_attention` 是未完成草稿，不是完整成功。
4. 在地图和每日卡片中查看行程；按需手工编辑。
5. 从历史记录直接问攻略或选择某一天让 Agent 改排，也可重新打开、筛选、删除或导出。
6. 投稿 PDF／图片后，由管理员解析、核对原文、保存复核版本，再发布。

## 🔧 核心实现

### LangGraph 生成

Agent 通过高德 MCP 获取候选并让模型按日返回结构化草稿；景点、天气、酒店和 RAG 上下文并行准备，多日模型调用按配置并发。随后 Agent 查询景点间路线、修复可确定的时间／步行约束并给出最终质量分类；Java 只校验协议、请求一致性和可信候选后按版本原子保存。

“我的行程”中的攻略问答使用端到端 SSE：检索完成后直接转发模型分片，最终以完整 `result` 收口；同一行程复用最近会话，减少无意义的会话写入。任务状态只展示排队中、生成中、失败和已取消，成功结果与未完成草稿统一进入行程记录。

### 链路延迟与超时

- Agent 使用 `trip_agent_stage_seconds{stage,outcome}` 记录 RAG、候选收集、模型生成和问答等阶段耗时，区分成功与降级，便于定位真正的等待点。
- 攻略问答的模型超时由 `LLM_RESEARCH_TIMEOUT` 控制，默认 15 秒；Java 到 Agent 的流式调用和浏览器请求还有各自的外层超时，客户端断开时会取消本次转发任务。
- 候选查询采用有界并发；路线由 Agent 在任务总截止时间内查询并纳入质量报告。上游不返回路线时明确标记 `ROUTE_UNAVAILABLE`，保存可继续调整的降级行程，不把未知耗时伪装成零。
- 单元测试和替身测试只证明并发、超时与降级路径可控；真实延迟和吞吐需在部署环境分别测量冷启动、热缓存及不同旅行天数的 P50/P95，不在文档中预设提升比例。

### Python 停机边界

前端只依赖 Java 就绪；Java `/readyz` 只检查数据库和本地必要配置。Python 停止时，登录、历史、分享和导出仍可使用；行程创建、景点发现、攻略问答和智能改排明确返回 `AGENT_UNAVAILABLE`，不会伪装成传统流程继续执行。`/api/capabilities` 分别报告 MCP 地图、Agent、RAG 和视觉能力。

### 任务可靠性

Java 使用有界执行池、数据库条件更新和幂等键。最终状态、历史保存和 outbox 同事务提交；保存前校验执行编号、截止时间、状态与原行程版本。断流不自动再次调用模型；重启中的任务标为 `PROCESS_INTERRUPTED`，用户可显式重试。

### 数据完整性、版本与审计

Flyway 在增加外键和状态约束前先检查孤儿数据与非法状态，发现历史脏数据就拒绝迁移，不会静默删除。行程的创建、编辑、核验、Agent 改排、助手确认和旧版恢复都会在业务事务内写入不可变版本；恢复旧版会生成一个新版本，仍需当前 `If-Match`。删除行程会在同一事务撤销公开分享、解除助手会话关联、写入 RAG 删除墓碑并清理含完整计划的版本快照，脱敏审计事件只保留资源、动作、版本、结果和请求 ID。

### RAG 与资料发布

Java 管理原文件、审核、业务版本和索引作业；Python 使用稳定向量 ID、版本水位和删除墓碑。检索后回查 Java，可见性校验失败时排除受影响资料并降级。重建从 Java 获取一致性快照，在新集合验证后核对变更序号再切换。

### 图片与导出

优先真实 POI 的最多三张候选图片。无图时，仅接受同城、3 公里内、名称前缀匹配且唯一的景区候选，并标注“景区参考图（非该具体点位实拍）”。图片校验格式、像素和大小，拒绝私网代理与远程 SVG；失败短缓存，占位图不做浏览器长缓存。卡片统一 8:5 铺满，普通照片适度裁切，参考图标注完整保留。

## 🛡️ 安全与数据边界

- 用户令牌、注销撤销、所有者校验、管理员审核均由 Java 负责。
- 原文件内部读取只接受资料 ID 与版本，不接受任意文件路径。
- 编辑后的开放时间、价格和路线不能自动变成“已外部核实”。
- 未知路线不等于零距离，缺失票价不等于免费；预算预留和假设会单独显示。
- 服务重启、取消和超时无法保证已发出的外部请求不计费。
- 单 Java／单 Agent／单机持久目录是明确边界；不支持水平扩容或跨主机自动故障接管。

## 📚 API 与监控

| 公开入口 | 用途 |
|---|---|
| `/api/auth/*`、`/api/preferences/me` | 登录、注销、身份与偏好 |
| `/api/trip/tasks*`、`/api/trip/plan*` | 任务、可操作状态筛选、兼容规划入口与 SSE |
| `/api/history*`、`/api/trips*` | 历史行程、乐观锁编辑、重新核验、版本查询与旧版恢复；旧手工接口仅保留兼容，不再提供前端入口 |
| `/api/favorites*` | 旧收藏兼容接口；不再作为当前产品流程入口 |
| `/api/trips/{id}/shares`、`/api/shared-trips/{token}` | 不可变只读分享、撤销和复制 |
| `/api/assistant/conversations*` | 历史行程内的 Agent 问答与改排会话；改排复用持久任务 |
| `/api/knowledge/*` | 投稿、复核、发布及作业状态 |
| `/api/map/*`、`/api/poi/*`、`/api/research/*` | 地图、图片与资料研究 |
| `/health`、`/readyz`、`/api/capabilities` | 存活、业务就绪与 Agent/MCP 分能力状态 |

版本接口为 `GET /api/trips/{id}/versions`、`GET /api/trips/{id}/versions/{version}` 和带当前 `If-Match` 的 `POST /api/trips/{id}/restore`。公开请求由 Java 处理；Python 仅提供 `/internal/v1`。Schema 与样例见 `contracts/internal-v1/`。Java 内部指标由 Actuator/Micrometer 暴露在 `/actuator/prometheus`，Prometheus 只通过容器网络抓取；日常四容器不默认启动额外监控栈，本地通知接收器仅用于验证，不是生产告警渠道。限流、并发舱壁、熔断、分层耗时与受控压测步骤见 [性能与雪崩保护手册](docs/operations/performance-reliability.md)。

## ✅ 自动化验证

```powershell
# Java：真实 PostgreSQL 集成测试需配置独立 TEST_DATABASE_URL，禁止使用日常业务库
cd business-backend
./mvnw.cmd test
```

```powershell
# 仓库根目录：临时隔离验证，不加载日常密钥或数据目录
docker compose -p trip-validation -f docker-compose.validation.yml up -d --build --wait postgres backend agent frontend
docker compose -p trip-validation -f docker-compose.validation.yml run --rm tests
python backend/scripts/java_api_contract_smoke.py --output evidence/public-api.json
python backend/scripts/java_knowledge_smoke.py --output evidence/knowledge.json
python backend/scripts/java_recovery_drill.py --output evidence/recovery.json
# 离线 QPS 阶梯、慢任务隔离和 Agent／高德故障演练
python backend/scripts/performance_drill.py --output docs/evidence/performance-current/report.json
```

前端目录执行 `npm ci`、`npx playwright install chromium`、`npx playwright test`。验证结束后运行 `docker compose -p trip-validation -f docker-compose.validation.yml down`，不常驻第二套项目。CI 还验证任务中断、取消后迟到及告警恢复。

V4 版本／审计改动的隔离验收基线为：Java 真实 PostgreSQL 测试 54 项、锁定依赖下 Python Agent 测试 205 项、HTTP 业务场景 20 项、Playwright 19 项全部通过；同时通过 Prometheus 规则、数据库恢复及 Agent 断联／迟到结果故障演练。这些结果证明当前固定场景和工程契约，不代表生产 SLA、吞吐或真实用户效果。

旧 Python 业务单测已由 Java 集成测试与公开接口验收承接；Agent 保留模型、MCP、RAG、解析、图片、协议及冻结场景测试。**清理前后测试数不能直接相加或比较为覆盖率。** 历史真实样本、迁移数据核对和当前清理证据见 [验收记录](docs/evidence/java-migration/README.md)。不以离线替身测试声称真实模型效果、实时事实准确率或生产性能。

## 💾 备份、恢复与清理

PostgreSQL 数据卷、`backend/data/knowledge_uploads`、`backend/data/chroma`、`backend/data/agent-runtime` 和私有配置需成套备份。恢复到独立库／目录核对，不直接覆盖日常环境。

详见 [当前运行手册](docs/operations/java-migration.md)。旧业务代码和历史部署脚本不再留在工作树；清理前源码提交为 `6c72a24`，本机另有仓库外源码备份。容器清理仅针对本项目辅助实例，不使用全局 prune，不删除其他项目容器或持久卷。

镜像清理在准确提交的 CI 通过后执行：只删除经项目标签／名称核对且不再使用的本项目镜像或验证标签。当前明确只维护 Java＋Agent 架构，旧 Python 架构回滚镜像已按用户决定删除；保留当前运行镜像、数据备份和历史源码，不删除其他项目镜像或共享基础镜像，不使用全局 image prune。

## ❓ 常见问题与局限

- **为什么还有 Python？** Python Agent 承担 MCP、模型、检索、解析、索引以及完整旅游生成；Java 承担公开地图入口、用户主动复核和业务状态，不再二次规划 Agent 结果。
- **为什么结果是草稿？** 候选、路线或约束未满足时诚实保留缺口，不把内容填满就算成功。
- **为什么某个景点无图？** 上游不保证每个点位有可用实拍；不会用无关图片冒充。
- **为什么首次检索较慢？** 可能需要创建城市索引和调用 embedding；更换模型还涉及维度兼容，不能直接清库。
- **地图不显示？** 核对前端 Web JS Key、安全配置和域名白名单，区分前端 Key 与后端 Web 服务 Key。
- **天气、预约、票价可靠吗？** 都有时间与来源边界；出发前仍需向官方确认。
- **能否直接扩容？** 当前仅支持单机单实例；多实例调度、跨主机锁和高可用需另行设计。

## 🤝 开发约定

修改协议时同步 Schema 和样例；修改业务能力时补 Java／公开接口测试；修改 Agent 时保留离线和故障用例。真实模型与地图验收需先确认预算，不把私有配置、用户数据、备份或未脱敏日志提交 Git。
