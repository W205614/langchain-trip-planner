# 🧭 智能旅行助手：Java 业务后端 + Python Agent

[![CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml)

基于 **Spring Boot、LangGraph 和 Vue 3** 的国内旅行规划应用。输入城市、日期与偏好，获取可查看、编辑和导出的逐日行程；规划任务可恢复，资料经过人工复核后才能进入公开检索。

**业务交给 Java，生成与检索交给 Python。** PostgreSQL 是业务唯一事实源，Python Agent 不包含业务 ORM、用户鉴权或公开业务路由。项目定位是可验证的单机应用，不是高可用平台，也不把静态攻略或模型输出当作实时旅行事实。

## ✨ 功能特点

- 🤖 **多日旅行规划**：LangGraph 并行获取景点、天气和酒店，再按日生成；使用经过校验的真实 POI，不用虚构地点填满行程。
- ⚡ **任务与流式进度**：Java 持久化排队、进度和结果；支持 SSE、刷新恢复、幂等提交、取消、超时与中断状态。
- 🧭 **约束与质量分类**：必去／排除、跨日去重、路线、步行、时间和预算由 Java 最终校验；区分完整、降级和未完成草稿。
- 🗺️ **地图与图片**：高德地图展示路线；图片按 POI ID 获取，无独立实拍时仅使用经过校验且明确标注的景区参考图，无可信来源则显示占位提示。
- ✏️ **行程编辑**：添加、删除、调整景点，局部改排；版本冲突不会静默覆盖已有修改，草稿需要明确确认后应用。
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

![历史行程管理](docs/screenshots/history.png)

## 🏗️ 技术栈

| 部分 | 技术 | 职责 |
|---|---|---|
| Java 业务后端 | Java 21、Spring Boot 4.0.3、Spring Security、MyBatis Starter 4.0.0、Flyway | 用户、业务 API、任务、确定性规则、资料审核、事务与 outbox |
| Python Agent | Python 3.11、FastAPI、LangChain、LangGraph | 内部执行协议、模型生成、地图适配、检索、解析及向量写入 |
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
                 ├── 上传目录：原文件与资料版本
                 └── /internal/v1 → Python Agent
                                      ├── LangGraph → 文本模型
                                      ├── 高德 MCP / REST
                                      ├── PDF / 图片解析 → 视觉模型
                                      └── RAG / embedding → Chroma

Python → Java 内部回查：资料可见性、原文件、重建快照与变更序号
```

- **Java 负责业务决定**：鉴权、额度、任务生命周期、规则裁决和数据提交。
- **Python 负责能力执行**：返回结构化行程、可信候选、进度、用量和降级信息；不写业务表。
- **内部接口不对公网开放**：独立服务密钥，固定地址；Nginx 不代理 `/internal/*` 或 `/metrics`，Agent 不发布宿主机端口。
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

编辑 `backend/.env` 中的 `LLM_*`、`AMAP_*`，按需要填写 `EMBEDDING_*`、`VISION_*`；编辑 `frontend/.env` 的 `VITE_AMAP_WEB_JS_KEY` 及对应安全配置。

```powershell
python backend/scripts/prepare_java_deployment.py
```

脚本生成 `deploy/runtime/postgres.env`、`business.env` 和 `agent.env`。新部署 JWT Key 留空时生成随机值；已有账户迁移必须保留原 Key。脚本拒绝覆盖已有目录，**已有日常环境不要重新生成配置**。

### 2. 构建并启动

```powershell
docker compose up -d --build --wait
docker compose ps
```

访问 **http://localhost:8080**。Flyway 自动初始化空业务库；不自动接管或 baseline 未知旧库。首次注册账号后可登录；审核管理员通过 `business.env` 的 `BOOTSTRAP_ADMIN_USERNAME` 显式指定已存在账号，重启 Java 生效。

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
| `deploy/runtime/business.env` | `JWT_SECRET_KEY`、`JDBC_DATABASE_URL`、`WORKERS_ENABLED`、`TRIP_*` | Java 独占业务数据库与用户鉴权 |
| `deploy/runtime/agent.env` | `LLM_*`、`AMAP_*`、`EMBEDDING_*`、`VISION_*`、`RAG_ENABLED` | 只包含能力服务配置，不传业务库凭据 |
| 两个服务 | `INTERNAL_SERVICE_KEY`、`AGENT_URL` / `BUSINESS_URL` | 固定服务地址，独立内部密钥 |
| 前端构建配置 | `VITE_AMAP_WEB_JS_KEY` 等 | 仅前端地图配置；不放服务端模型 Key |

日常新配置默认启用 Java workers，预算验收模式默认关闭。受控验收通过持久 `ACCEPTANCE_BUDGET_FILE` 分类别限制实际调用；其开启、上限与停止规则必须单独确认。不要删除账本来重置额度。

**现有 Chroma 的模型和向量维度必须一致。** 更换 embedding 模型不能仅改环境变量；需备份后执行受控重建，失败保留旧集合，不直接删除索引。

## 📝 使用指南

1. 注册并登录，选择城市、日期、交通、住宿和旅行偏好。
2. 填写必去／排除景点与步行等约束，提交后查看任务进度。
3. 先看结果分类与缺口：`needs_attention` 是未完成草稿，不是完整成功。
4. 在地图和每日卡片中查看行程；按需手工编辑或局部改排。
5. 从历史记录重新打开、筛选、删除或导出行程；偏好需要主动选择保存。
6. 投稿 PDF／图片后，由管理员解析、核对原文、保存复核版本，再发布。

## 🔧 核心实现

### LangGraph 生成

景点、天气、酒店节点获取候选后汇合；模型按日返回结构化草稿。无效输出、超时或候选不足会暴露降级信息。Java 再查询实际路线并完成确定性规则裁决，生成与外部调用不占用数据库事务。

### 任务可靠性

Java 使用有界执行池、数据库条件更新和幂等键。最终状态、历史保存和 outbox 同事务提交；保存前校验执行编号、截止时间、状态与原行程版本。断流不自动再次调用模型；重启中的任务标为 `PROCESS_INTERRUPTED`，用户可显式重试。

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
| `/api/trip/tasks*`、`/api/trip/plan*` | 任务与兼容规划入口、SSE |
| `/api/history*` | 历史、编辑、改排与草稿应用 |
| `/api/knowledge/*` | 投稿、复核、发布及作业状态 |
| `/api/map/*`、`/api/poi/*`、`/api/research/*` | 地图、图片与资料研究 |
| `/health`、`/readyz` | 存活与业务就绪 |

公开请求由 Java 处理；Python 仅提供 `/internal/v1`。Schema 与样例见 `contracts/internal-v1/`。内部 Prometheus 指标与告警规则位于 `deploy/`，日常四容器不默认启动额外监控栈；本地通知接收器仅用于验证，不是生产告警渠道。

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
```

前端目录执行 `npm ci`、`npx playwright install chromium`、`npx playwright test`。验证结束后运行 `docker compose -p trip-validation -f docker-compose.validation.yml down`，不常驻第二套项目。CI 还验证任务中断、取消后迟到及告警恢复。

旧 Python 业务单测已由 Java 集成测试与公开接口验收承接；Agent 保留模型、MCP、RAG、解析、图片、协议及冻结场景测试。**清理前后测试数不能直接相加或比较为覆盖率。** 历史真实样本、迁移数据核对和当前清理证据见 [验收记录](docs/evidence/java-migration/README.md)。不以离线替身测试声称真实模型效果、实时事实准确率或生产性能。

## 💾 备份、恢复与清理

PostgreSQL 数据卷、`backend/data/knowledge_uploads`、`backend/data/chroma`、`backend/data/agent-runtime` 和私有配置需成套备份。恢复到独立库／目录核对，不直接覆盖日常环境。

详见 [当前运行手册](docs/operations/java-migration.md)。旧业务代码和历史部署脚本不再留在工作树；清理前源码提交为 `6c72a24`，本机另有仓库外源码备份。容器清理仅针对本项目辅助实例，不使用全局 prune，不删除其他项目容器或持久卷。

镜像清理在准确提交的 CI 通过后执行：只删除经项目标签／名称核对且不再使用的本项目镜像或验证标签。保留当前运行镜像和明确标记的回滚镜像；不删除其他项目镜像或共享基础镜像，不使用全局 image prune。

## ❓ 常见问题与局限

- **为什么还有 Python？** Python 仅承担 Agent、地图、检索、解析和索引能力；用户、任务、审核与业务数据由 Java 管理。
- **为什么结果是草稿？** 候选、路线或约束未满足时诚实保留缺口，不把内容填满就算成功。
- **为什么某个景点无图？** 上游不保证每个点位有可用实拍；不会用无关图片冒充。
- **为什么首次检索较慢？** 可能需要创建城市索引和调用 embedding；更换模型还涉及维度兼容，不能直接清库。
- **地图不显示？** 核对前端 Web JS Key、安全配置和域名白名单，区分前端 Key 与后端 Web 服务 Key。
- **天气、预约、票价可靠吗？** 都有时间与来源边界；出发前仍需向官方确认。
- **能否直接扩容？** 当前仅支持单机单实例；多实例调度、跨主机锁和高可用需另行设计。

## 🤝 开发约定

修改协议时同步 Schema 和样例；修改业务能力时补 Java／公开接口测试；修改 Agent 时保留离线和故障用例。真实模型与地图验收需先确认预算，不把私有配置、用户数据、备份或未脱敏日志提交 Git。
