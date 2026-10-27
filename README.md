# AI 旅行规划助手

基于 **Vue 3、Spring Boot 和 LangGraph** 的国内旅行规划应用。填写出发地、目的地、日期、人数、预算与偏好，生成可以查看、编辑和导出的逐日行程。

它把景点查询、路线安排、预算整理和出行记录集中到一个应用中，帮助你从“到处查攻略”走到“有一份可以继续调整的旅行计划”。

[快速部署](#快速部署) · [第一次使用](#第一次使用) · [界面预览](#界面预览) · [常见问题](#常见问题)

## 能帮你做什么

| 需求 | 对应功能 |
|---|---|
| 不知道每天怎么玩，景点是否顺路 | 输入每日时间、预约与酒店，按有向路线和时间窗口生成多日时间轴，展示交通、等待、游览和用餐 |
| 跨城时怕赶不上车 | 分段填写城市及自己确认的交通班次、到离时间和位置；每日时长上限计入固定交通与原城接驳，不会从抵达后重新计时；不接真实查票或购票 |
| 计划变了，不想全部重做 | 选择单活动、时段、当天剩余或明确多日范围；锁定和已完成安排受保护，改排先预览差异，再确认或放弃 |
| 生成过程较长，担心刷新丢失 | 持久化任务及有效阶段，在“我的行程”查看、取消或显式恢复；恢复创建关联的新执行，旧执行不能写回 |
| 攻略、历史行程分散 | 保存与筛选历史、查询攻略；可选接入知识检索，PDF／图片资料经过人工复核后发布 |
| 同行人需要一起看、一起准备 | 分享行程，记录行前核验、预订事项和实际费用；社区行程审核后公开 |
| 出门后想随时查看 | 导出 PNG、PDF 或离线 HTML |

行程区分可行、信息不足、计算预算内未找到和当前模型已证明无解。缺失路线可能以明确标注的保守时间占位，不能据此认定可行。费用区分确认、估算与未知；关键费用未知时不会报告总预算已满足。天气、营业和票价仍需出发前核实。 系统推荐的餐厅若无法放入时间轴，会列出餐别、餐厅和就近用餐的待办；用户明确设置的用餐窗口、预约与锁定仍必须保留。

## 界面预览

以下为仓库已有的功能截图，具体布局和字段以当前运行页面为准。

**填写旅行需求**

![旅行需求表单](docs/screenshots/home.png)

**查看每日安排、预算与地图**

![行程概览与地图](docs/screenshots/result.png)

<details>
<summary>展开查看行程详情与历史记录</summary>

![每日行程详情](docs/screenshots/result1.png)

![历史行程管理](docs/screenshots/history.png)

</details>

## 快速部署

下面使用 Docker 在本机启动。命令以 **Windows PowerShell** 为例，均在项目根目录执行；已有部署升级请先看[运行与迁移手册](docs/operations/java-migration.md)，不要覆盖原配置。

### 1. 准备环境并下载项目

需要安装并启动 **Docker Desktop**（包含 Docker Compose），另外准备 **Git、Python 3.11+ 与 pip**。Python 仅用于生成部署配置；Java、Node.js 和应用依赖由镜像构建，无需在本机单独安装。

还需要可用的文本模型 API、高德 Web 服务 Key，以及用于浏览器地图的高德 Web 端（JS API）Key。构建需要联网下载镜像与依赖，生成行程需要访问模型服务和高德 MCP，相关调用可能产生费用。

```powershell
git clone https://github.com/W205614/langchain-trip-planner.git
cd langchain-trip-planner
Copy-Item backend/.env.example backend/.env
Copy-Item frontend/.env.example frontend/.env
python -m pip install python-dotenv
```

### 2. 填写必要配置

用文本编辑器打开以下两个文件，将示例占位值替换为自己的配置：

| 文件 | 配置项 | 填写内容 |
|---|---|---|
| `backend/.env` | `LLM_MODEL_ID` | 模型供应商提供的文本模型名称 |
| `backend/.env` | `LLM_API_KEY` | 对应的模型 API Key |
| `backend/.env` | `LLM_BASE_URL` | 供应商提供的 OpenAI 兼容 API 基础地址 |
| `backend/.env` | `AMAP_REST_API_KEY` | 高德 Web 服务 Key，供 Java 使用 |
| `backend/.env` | `AMAP_MCP_API_KEY` | 高德 MCP 使用的 Web 服务 Key；开发时可与上一项相同 |
| `frontend/.env` | `VITE_AMAP_WEB_JS_KEY` | 高德 Web 端（JS API）Key，用于显示地图 |

`LLM_BASE_URL` 按供应商的接口说明填写，通常包含 `/v1`，不要填成完整的 `/chat/completions` 请求地址。前端地图 Key 与服务端 Web 服务 Key 类型不同，不能混用；模型 Key 只放在后端配置。

**首次只体验行程规划时，将 `backend/.env` 中的 `RAG_ENABLED` 改为 `false`**，其余未列出的配置可先保留模板值。以后需要知识检索，再配置供应商支持的 `EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 并开启 RAG；图文资料解析还需要相应的 `VISION_*` 配置。文本模型服务不一定同时支持嵌入和视觉模型。

### 3. 生成配置并启动

```powershell
python backend/scripts/prepare_java_deployment.py
docker compose up -d --build --wait
docker compose ps
```

配置脚本会生成私有的 `deploy/runtime/postgres.env`、`business.env` 和 `agent.env`，自动创建数据库密码、JWT 与内部服务密钥。**只在首次部署执行，不要删除已有目录后重新生成，也不要提交这些私有文件。** 数据库表由 Flyway 在启动时自动初始化。

首次构建需下载依赖，耗时取决于网络与机器。`docker compose ps` 中应看到 `frontend`、`backend`、`agent`、`postgres` 四个服务，启动完成后健康状态应为 `healthy`；容器健康不代表外部 API Key 已验证可用。

打开 **[http://localhost:8380](http://localhost:8380)**，注册自己的账号并登录即可使用，无默认账号密码。默认仅允许本机访问。

如果 8380 已被占用，可先在项目根目录新建 `.env`，写入 `APP_PORT=8381`，再执行启动命令，之后访问 `http://localhost:8381`。

## 第一次使用

1. 注册并登录后，在首页填写出发地、目的地、未来的出行日期、人数、房间数、总预算和偏好。建议先从一个城市、一天行程开始熟悉流程。
2. 提交规划后查看进度；离开页面或刷新后，可以从“我的行程”继续查看任务与结果。“任务”默认显示需要操作的任务，切换“全部任务”可查看已保存结果和普通草稿；普通草稿可以显式恢复有效阶段。已生成的助手提案应打开方案确认或放弃，不能通过通用任务恢复或重新提交处理。
3. 展开时间约束：填写每日出发／结束、首末日到离时间、酒店和预约。预约可选固定活动时段或允许入场窗口；跨城需填写城市分段及自己确认的交通，不会自动查票。国内默认时区 `Asia/Shanghai`。
4. 打开结果，先检查时间轴、事实来源和未解决条件。所有者可锁定活动内容／时段或标记完成；编辑者保留手工调整和账本权限，查看者只读。手工修改相关日期后，旧时间轴会提示重新核验。
5. 在对话中选定允许修改的活动、日期或时段，提出需求。先比较新增、移除、移动、路线及费用变化，再确认；确认写入预览过的方案。原版本、约束、预约或证据变化会阻止过期提案生效，页面会保留提案并区分原因：事实过期先重新核验，版本或规划输入变化则重新加载原行程并生成提案。局部改排后的全程状态仍保留其他日期的冲突、未完成计算和未知条件。
6. 调整满意后导出 PNG、PDF 或离线 HTML；需要同行协作时，再进入行程工作台整理核验、预订与费用。资料核验只提示风险及受影响活动，不会自动改写行程。

## 常用操作与配置更新

以下命令仍在项目根目录执行：

```powershell
# 查看启动问题与最近日志
docker compose logs --tail 100 backend agent frontend

# 暂停服务，保留数据
docker compose stop

# 再次启动
docker compose up -d --wait
```

首次生成配置后，Compose 实际读取的是 `deploy/runtime/*.env`，后续修改 `backend/.env` 不会自动同步到运行配置：

| 修改内容 | 修改位置 | 生效命令 |
|---|---|---|
| 文本模型、RAG、视觉配置 | `deploy/runtime/agent.env` | `docker compose up -d --force-recreate agent` |
| Java 服务端高德 Key | `deploy/runtime/business.env` 的 `AMAP_REST_API_KEY` | `docker compose up -d --force-recreate backend` |
| Agent 高德 Key | `deploy/runtime/agent.env` 的 `AMAP_API_KEY`（脚本从 `AMAP_MCP_API_KEY` 转入） | `docker compose up -d --force-recreate agent` |
| 前端地图 Key | `frontend/.env` 的 `VITE_AMAP_WEB_JS_KEY` | `docker compose up -d --build frontend`，完成后刷新浏览器 |

`docker compose restart` 不会重新载入修改后的环境文件，应使用表中的重建容器命令。不要使用 `down -v` 或删除 `backend/data` 来排查问题；数据库卷、上传文件、索引及私有配置需要成套备份，详见[运行手册](docs/operations/java-migration.md)。

## 常见问题

- **页面打不开或启动失败？** 先确认 Docker Desktop 正在运行、已生成 `deploy/runtime`，再查看 `docker compose ps` 和日志。端口冲突可按上文修改 `APP_PORT`；若首次构建失败，先检查镜像和依赖下载网络。
- **能登录，但无法生成行程？** 检查 Agent 的运行配置中模型名称、API 地址、Key 是否匹配，以及高德 MCP 是否可访问。`AGENT_UNAVAILABLE` 表示 Agent 不可用；登录和历史查看仍可使用。
- **地图不显示或景点没图片？** 核对前端 JS API Key 的类型与高德控制台的安全配置、域名限制，修改 Key 后重新构建前端。某些景点没有可信图片时会显示占位提示。
- **为什么有资料缺口或降级结果？** 可能是路线、餐饮、天气或知识资料不足；未启用 RAG 时也可能缺少攻略上下文。请查看具体提示，调整或补充核验，不要把草稿当作完整方案。
- **中断后恢复是否重新扣费？** 兼容且未过期的阶段可以复用；已发送但尚未持久化响应的调用按不确定处理。新调用仍先预留预算，失败不退计数，不承诺费用 Exactly Once。重复点击恢复使用同一幂等键不会创建多份执行。
- **30 天是否都能算出最优结果？** 输入仍支持 1–30 日，但求解器有候选、路线查询及总时间上限。达到上限会保留有效阶段并说明原因；不承诺全局最优，也没有用短行程测试证明全部长度的性能。
- **需要资料审核管理员？** 先注册账号，再在 `deploy/runtime/business.env` 添加 `BOOTSTRAP_ADMIN_USERNAME=已注册的用户名`，执行 `docker compose up -d --force-recreate backend`。
- **可以直接部署到公网吗？** 本指南面向本机单实例使用；公网部署还需配置 HTTPS、访问入口和备份策略。当前不提供多实例高可用部署。

## 技术栈与进一步阅读

前端使用 Vue 3、TypeScript 与高德 JS API；Java / Spring Boot 负责用户、行程、任务与审核，Python / LangGraph 负责候选生成、高德 MCP 和检索，OR-Tools 负责有界时间窗口求解；PostgreSQL 保存业务数据，Chroma 保存向量索引。Docker Compose 默认启动四个服务，仅前端发布本机端口。

- [运行、升级与备份恢复](docs/operations/java-migration.md)
- [时间规划、受限改排与恢复运维](docs/operations/final-planning.md)
- [本轮验收证据与边界](docs/evidence/final-planning-20261020/README.md)
- [2026-10-25 定向改善与效果证据](docs/evidence/improvement-20261025/README.md)
- [同行协作、预订与费用工作台](docs/operations/trip-operations.md)
- [高德 MCP 接入说明](docs/amap-mcp.md)
- [性能与可靠性说明](docs/operations/performance-reliability.md)
- [历史验收与交付记录](docs/evidence/java-migration/README.md)

## 规划、账号与运行治理改善

当前改动覆盖核验、规划有效性、多轮澄清、资料问答、上传配额和单机运行治理。验收状态逐项记录在 [0—12 范围账本](docs/evidence/optimization-20261026/scope-ledger.json)；未完成的浏览器、部署、依赖扫描或外部条件不能计作已验收。

- **核验保留原内容：** 地图超时、限流和连接失败只更新核验结果；明确不存在的景点进入待替换状态。替换需先选择可信候选、预览，再保存编辑草稿，锁定和已完成安排继续受服务端保护。
- **时间轴有依赖期限：** 每日排程绑定规划条件、旅行事实及其有效期。修改营业、住宿、预约或每日时间后，受影响日期显示待重新核验；旧时间不能继续用于新时段锁或完成操作。导出会复核权限并标明过期状态，保留的旧内容不等于重新通过检查。
- **事实补全可操作：** 行程工作台可填写营业窗口、预约、住宿端点、提前到站、自理用餐和父子景点关系，注明来源和有效期后发起局部求解提案。用户声明始终标为用户提供，不能变成官方信息；不同 POI 编号也不直接证明可以独立游览。
- **助手保留澄清状态：** “轻松一点 → 第2天”接续同一个修改需求，支持取消、改意和到期；原行程仍只在预览确认后更新。
- **资料检索与问答分开：** 纯检索不发送回答模型请求；生成问答展示答案、来源和降级原因。引用校验采用保守原文支持检查，不能证明来源真实、问题相关或完全消除幻觉。演示和过期资料不作为当前旅行事实。
- **浏览器会话：** 新浏览器登录使用 HttpOnly Cookie、SameSite 与绑定会话的 CSRF 校验，保留旧 API Bearer 兼容。账号安全页支持修改密码、一次性离线恢复码和验证器 TOTP；开启或恢复时撤销旧凭证。离线恢复码只显示一次，请私下保存。邮件/SMS 通道和真实异机演练尚需外部配置。
- **上传有累计上限：** 持久化预留限制频率、累计字节、待审数量与剩余磁盘空间。同用户、同内容和相同元数据才复用；不确定提交保留文件及待核对记录，不凭超时删除可能已提交的原件。管理员可检查核对结果。

公网使用必须经过 HTTPS 入口。`ACCOUNT_SECURITY_ENCRYPTION_KEY` 是独立的 32 字节 Base64 密钥，新配置自动生成；原配置升级时仅在缺失时生成，已有值不能随意更换。它须与数据库和私有环境一起备份，丢失会影响已启用的 TOTP。`ADMIN_MFA_REQUIRED=true` 时，未完成验证器设置的管理员只能先进入账号安全流程，不能跳过管理权限检查。

### 升级与全新部署

默认 `docker-compose.yml` 是既有单机环境的升级入口，必须保留原挂载。`docker-compose.production.yml` 现在是独立的新环境，默认项目名为 `trip-production`，读取 `deploy/runtime-production/*.env`，不能通过换 Compose 文件来迁移已有资料。新环境先执行：

```powershell
python backend/scripts/prepare_java_deployment.py --output deploy/runtime-production
docker compose -f docker-compose.production.yml --profile monitoring up -d --build --wait
```

新配置会分配 `DATASET_ID`。业务数据库、上传与 Agent 持久目录的标记不匹配会拒绝启动。该标记证明环境身份，不能单独证明恢复时点一致；恢复和升级仍须核对成套备份的内容。

既有数据首次启用登记时，应在维护窗口停止写入，保留 PostgreSQL 可读，先成套备份，再执行检查。以下示例路径必须替换为本次实际备份：

```powershell
python backend/scripts/backup_java_deployment.py --output backups/pre-optimization-YYYYMMDD-HHMM --archive-images
python backend/scripts/enroll_dataset.py --backup backups/pre-optimization-YYYYMMDD-HHMM --runtime deploy/runtime
# 上一步校验原表原列与三套持久文件后，显式登记同一数据集：
python backend/scripts/enroll_dataset.py --backup backups/pre-optimization-YYYYMMDD-HHMM --runtime deploy/runtime --execute
```

登记后还须准备本次私有运行配置和镜像，再使用明确的 Compose 文件启动。现有 `prepare_java_deployment.py --upgrade-existing` 会优先读取 `backend/.env` 的地图配置并选择 MCP 传输，使用前必须核对这是否符合原环境；本轮日常升级从原容器的有效配置准备新的私有文件，保留原供应商、凭据、传输和预算，没有直接运行该命令。实际镜像与挂载见[脱敏部署记录](docs/evidence/optimization-20261026/daily-effective-deployment-public.json)。不要为修复登记失败切换空卷、清库或删除原目录。旧镜像连接已迁移数据库不等于完成数据回退；应从已验证备份恢复到新的隔离数据库和文件目录。恢复工具保留旧、新克隆并报告精确资源，不自动清理。

### 备份、告警与有界资源

容器已配置 CPU、内存、PID 与日志轮转；这些是资源预算，不是峰值容量证明。终态任务正文默认保留 90 天，过期快照延迟 24 小时分批清理；保留任务身份、恢复关联、预算账本、未完成作业与必要删除证据。超过保留期的任务需从当前行程发起新执行。

生产 monitoring 配置连接 Prometheus、Alertmanager 和有界本地持久收件箱，提供分组、去重、恢复通知与重复间隔。外部人工作用通知渠道需另行配置，不能把本地收件箱称为已送达真实值班人员。

自动备份在受控维护窗口短暂停写并恢复原运行状态，可复制到用户配置的 UNC 目标。默认只检查，不执行；先检查任务参数，再安装当前用户的 Windows 计划任务（依赖该用户会话与 Docker Desktop）：

```powershell
python backend/scripts/automated_maintenance.py backup
python backend/scripts/automated_maintenance.py backup --execute --replica-root \\backup-server\private-trip-backups
pwsh -File deploy/install-backup-task.ps1 -PythonPath C:\path\to\pythonw.exe
# 核对参数后加 -Install 才会注册每日任务；不覆盖已有任务。
```

备份目标为 RPO 24 小时、RTO 1 小时，属于目标而非已实现承诺；实际恢复耗时和异机证据见交付记录。验证复制不等于验证故障域独立。调度器失锁后停止领取并拒绝旧执行结果，恢复脚本只检查所选容器并实施受控重启，不能据此宣称多实例高可用。

### 评测和安全证据

新增真实验收使用固定服务端批次和持久样本登记：模型最多 12 次、Embedding 40 次、高德 REST 120 次、Token 预留 800000，完整流程最多 4 条且开发/留出各 2 条；旧有更严格额度优先。失败和不确定发送不退额度，不能换编号、换库或清账本获得额度。该批次禁止 MCP，避免把一次工具调用当成一次 REST；浏览器地图 SDK 单列。预留不等于供应商账单。

CodeQL、依赖、密钥与镜像检查按精确提交运行，扫描失败不作为零漏洞；具体未修复条目及适用性分析须保留。合成反例、离线 HTTP、真实浏览器、真实供应商和人工评审分开记证。[独立人工评审表](docs/evidence/optimization-20261026/blind-review/review.html)尚无真人评分，不能用程序通过率替代真实旅行事实准确率。

首次完整 CI 的[失败结果](docs/evidence/optimization-20261026/first-ci-d323a44-results.json)保留：SARIF 解析器补齐官方扩展规则引用后，实际报告的 24 项高危告警经[独立源码与数据流复核](docs/evidence/optimization-20261026/codeql-independent-source-review-24.md)。其中 22 项只对精确查询、位置、源码、数据流和到期时间作上下文分类，保留原始等级和数量；它们与 8 项未修复 CVE 的临时风险接受相互独立。新路径、新数据流、源码变化、未知或过期审查继续阻断，两项扫描函数命名误报仅作内部改名，不加例外。[第二轮 CI](docs/evidence/optimization-20261026/second-ci-5485f8a-results.json)实际验证四种语言门禁通过，原始高危 22 项、已审上下文 22 项、阻断 0 项；替换提交仍须重新验证。原始 SARIF 继续上传，门禁通过不代表 GitHub 没有原始告警。

CI 中两份旧 Python 夹具补齐每日交通和合成事实有效期，[全部原断言保留](docs/evidence/optimization-20261026/ci-fixture-contract-review.json)。一份 HTTP 场景的[纯 Java 复现](docs/evidence/optimization-20261026/scope-projection-contract-review.json)只发现存储与读取投影的过期原因码不同，两边仍都过期、未核实，其余日期字段一致；这不代替原 CI 数据抓取。第二轮 CI 保留原整天相等断言并[抓取实际两份日期](docs/evidence/optimization-20261026/ci-excluded-day-actual-review-5485f8a.json)，逐字段确认唯一差异确为该原因码。此后只对这两个已审代码作表示比较，两边必须明确过期、未核实，其他全部字段、类型和数组顺序仍严格相等；额外差异继续失败。夹具及诊断显式发生在临时 CI 测试副本中，批准的业务源码与镜像文件未改；首轮失败不改写成新一轮通过结果。时间窗口基准也只补足三条明确的[合成预约事实](docs/evidence/optimization-20261026/time-window-benchmark-fixture-review.json)，原六个期待结果和断言未改。输出明确标记输入夹具已修订，不能与原冻结评测直接比较，也不改变此前 16/17 留出集记录；这些声明不是真实景点事实。

[第三轮 CI](docs/evidence/optimization-20261026/third-ci-e44acfb-results.json)通过 15/16 项任务，浏览器为 51 项通过、7 项失败，原失败和 trace 保留。[逐项核对](docs/evidence/optimization-20261026/ci-browser-contract-review.json)确认旧夹具缺少会话历史和额度读取、仍拦截旧检索地址，或在主动令时间轴过期后要求锁定。CI 仅在显式准备的完整测试副本中修订五份原测试，全部 58 个原测试名称、超时及跳过标记保持不变；角色锁定/完成正例保留，并增加过期后的界面禁用、真实 HTTP 拒绝及状态不变检查。移动端验证完整七个入口及原有几何约束。四项副本守卫测试与五份 TypeScript 语法检查通过，新的浏览器结果仍待最终 CI。恢复、发布、通知和重启检查在隔离栈启动成功后独立收集结果，前序步骤失败仍使整个任务失败。

当前 CI 使用下列明确的契约迁移层。未经适配的冻结原脚本与适配后的执行是两份证据，原始失败仍成立；直接运行原 `pytest`、时间窗口基准或浏览器套件不等同当前 CI 流程，也不能宣称原套件全部通过。

| 入口 | 精确适配范围 | 结果与保留边界 |
|---|---|---|
| `tools/ci_contract_fixture_overlay.py` | 两个旧 Python 测试的每日交通、缓存观察时间与有效期 | 原行为断言保留；只写 CI 临时检出目录中的两份测试文件，映射前后哈希写入 `evidence/ci-fixture-overlay.json` |
| `tools/ci_time_window_benchmark.py` | 原六场景只补三条合成“不需预约”声明 | 原 `run`、期待结果和断言不改；输出标记 `current-contract-fixture-20261027`、输入已变且不可与原冻结评测比较，留出 16/17 记录不变 |
| `tools/ci_excluded_day_contract.py` | 原 HTTP 场景中两个已审的派生失效原因码 | 两份日期必须明确过期、未核实，其他字段、类型、顺序全部严格相等；诊断模式仍执行原整份 JSON 相等断言 |
| `tools/ci_browser_contract_overlay.py` | 五份原测试中的七个旧前提，完整复制后执行全部 58 项 | 原文件、测试名称、超时和跳过标记不改；精确替换见 `tools/ci_browser_contract_replacements.json`，保留副本哈希和浏览器 trace |

[第四轮实际结果](docs/evidence/optimization-20261026/fourth-ci-478da94-results.json)为浏览器 57 项通过、1 项失败，四段后续恢复检查均通过。剩余浏览器失败是新增名称断言遗漏组件在“退 出”中插入的空格，已按实际可访问名称精确修正；全部几何检查仍保留，完整布局结果须等下一轮。密钥门禁的两条记录已重新计算为公开源码指纹，[窄例外和负例](docs/evidence/optimization-20261026/ci-secrets-public-source-hash-review.json)只覆盖指定凭据文件中的两个确切值；其他值或路径仍阻断，完整历史扫描仍须由最终提交通过。

这些入口默认只核对源码哈希，不执行应用、不创建测试副本。以下只读检查可从仓库根目录复现；它们通过仅代表适配前提匹配：

```powershell
python tools/ci_contract_fixture_overlay.py
python tools/ci_time_window_benchmark.py
python tools/ci_excluded_day_contract.py
python tools/ci_browser_contract_overlay.py
docker compose -p trip-validation -f docker-compose.validation.yml config --quiet
```

完整执行复用 [CI 工作流](.github/workflows/ci.yml) 的 `Python Agent tests` 与 `Docker Compose config and build` 任务，后者使用[既有隔离 Compose](docker-compose.validation.yml)、专用项目/数据卷、离线供应商和测试账号，不读取日常业务挂载。工作流向 `main` 的提交或 PR 触发；在 Actions 中对同一提交重跑全部任务，可复现包含测试前提、管理员环境、完整浏览器套件及后续恢复的实际顺序。写入/执行开关只在 `GITHUB_ACTIONS=true` 的临时 CI 检出目录中使用，完整命令以工作流为准；单独调用其中一条不能代表整条验收。镜像构建另按既有归档恢复 12 个历史输入，并固定已审基础镜像，以复现既有受控演示产物，不将历史输出当作新成绩。

这些迁移层服务于本次冻结版本的审查可追溯性。后续若确认真实应用缺陷，应修正应用及原测试、重新审查对应契约并逐步移除过时适配，保留旧失败与修订依据。源码、系统包、ELF、应用文件或运行权限变化后，须重新评估受影响风险和验证证据；现有精确组合的临时接受不会自动覆盖新组合，允许指纹不能自动重算求绿。最终交付要求同一替换提交全部 16 项 CI/安全任务实际成功。

安全门禁允许明确记录的情境评估，不能读作“零风险”。Chroma 的四项无补丁上游问题仅在本项目显式嵌入式客户端、入口和源码指纹均匹配时适用评估；Debian 已回补条目、缺失的漏洞组件和仍受影响但供应商评级 Minor 的条目分开记录，后者仍未修复。未知条目、扫描器错误、版本或指纹不匹配均阻断门禁。供应商评级本身不能放行这 8 项：用户已明确暂时接受精确核验组合中的残余风险，**仅限本机受控演示，到北京时间 2026-11-09 23:59:59 失效，不授公开生产许可**。授权与冻结指纹分别见[原始决定的公开副本](.github/security-risk-authorization.json)和[临时例外记录](.github/security-risk-acceptance.json)。

门禁逐项核对批准业务源码树、依赖锁与 Docker recipe、87 个系统包版本、1086 个原生 ELF 路径指纹、135 个应用文件指纹及现场运行约束；原生 ELF 比较原始字节，明确列出的 UTF-8 业务文本仅规范 CRLF/LF。CI 必须重新扫描精确镜像并实测默认 uvicorn 进程，不能以镜像标签或旧报告代替等价证明。过期、源码/组件/权限组合变化、额外源代码挂载、加载器覆盖、未知高危或扫描失败仍阻断，禁止自动刷新允许指纹。`pass_temporary_risk_acceptance` 保留未修复分类与 52 条记录/8 项 CVE；原始 Agent High/Critical 总数仍为 79。逐项影响和未知见[部署矩阵](docs/evidence/optimization-20261026/unresolved-cve-deployment-matrix.md)，扫描与证据边界见[安全供应链](docs/evidence/optimization-20261026/security-supply-chain.json)。

Agent 的日常及独立生产配置显式丢弃所有 Linux capabilities，并禁止通过 SUID/file capabilities 获得新权限；镜像精确移除无业务用途的 mount/umount/nsenter/unshare 及已核别名/副本，保留系统依赖库。根文件系统设为只读，runtime、Chroma 使用原有持久挂载，临时目录与应用文件日志分别使用 128 MiB/64 MiB tmpfs；应用文件日志随容器停止消失，控制台日志仍由 Docker 按既定容量轮转。这些约束缩小提权与写入面，不修复进程内原生库的越界写或 UAF，实际 uvicorn 候选验收结果须与对应镜像核对。旧 Bearer 凭证继续遵守签发时的 `exp` 与 `token_version`；新会话的较短到期配置不会自动撤销全部历史凭证。

Agent 当前对 `libacl1` 和 `zlib1g` 使用两个经过签名索引及文件哈希核验的 Debian 安全版本；安装器要求只有这两个包升级、不增删包、不升级 libc 或工具链，并检查原生消费者兼容性。构建不会保留 sid 仓库入口。这增加了版本维护责任：[包锁](backend/runtime-security-packages.json)和[供应商评估](.github/security-vendor-policy.json)于 **2026-11-09** 到期，届时须复核稳定发行版回补并更新证据，不能关闭门禁继续沿用。监控派生镜像同样固定官方源码/发布物及最小依赖修补，保留原版对照扫描。

本轮用户暂未提供独立异机备份目标，真实异机恢复验收已明确暂缓。首次离线留出原始结果保留为 16/17，另附身份表示评分假设的审计；不将事后解释或同样本复测改写成新的留出成绩。

2026-10-27 的日常切换保留原 PostgreSQL 卷和上传、Chroma、runtime 三处绑定目录，继续监听 `127.0.0.1:8380`。[切换前完整备份](docs/evidence/optimization-20261026/daily-fresh-backup.json)、[四镜像归档闭包](docs/evidence/optimization-20261026/latest-daily-image-archive-closure.json)和[最新旧版本独立恢复](docs/evidence/optimization-20261026/latest-daily-restore.json)均已验证。26 张原表按原字段比较，其中 24 张一致；Flyway 原记录前缀一致并追加 V11—V14，38 条过期超过 24 小时的执行检查点被保留策略清理，原件仍在备份中，详见[数据核对](docs/evidence/optimization-20261026/daily-post-upgrade.json)。

[日常业务读回](docs/evidence/optimization-20261026/daily-readback-result.json)实际验证旧 Bearer、新 Cookie、CSRF 拒绝、本人历史及版本读取、页面、HTML 导出和退出撤销。使用一个升级前新建的普通验收账号及一份公开行程副本；没有冒用原私人账号。该旧副本没有时间轴，无法提供时间锁拒绝的现场证据；[首次失败与假设审计](docs/evidence/optimization-20261026/daily-readback-run1-adjudication.json)保留，不冒称日常完整规划链路已重新验证。

Windows 任务 `TripPlanner-AutomatedBackup` 已注册为当前用户、有限权限、每日北京时间 03:00，禁止并行实例；[首次实际执行](docs/evidence/optimization-20261026/windows-backup-task-result.json)退出码 0，备份与服务恢复阶段约 231 秒，原四容器 ID、镜像和运行状态一致。任务依赖用户登录和 Docker Desktop，宿主机关闭时不提供连续备份保证。备份保留工具只提出待复核候选，不自动删除历史备份，需要结合磁盘告警人工处理。

随后一次开机后的 [08:46 补执行失败](docs/evidence/optimization-20261026/windows-backup-20261027-startup-failure.json)，服务恢复也失败；用户随后确认这些容器由本人手动停止；不能把这次停机或受其影响的失败归因于项目自行停机。保存原始日志后，本项目按精确原 ID 恢复。现在任务改用宿主入口 `tools/windows_backup_runner.pyw`：外层仍为原 `pythonw.exe`，每个 Docker 子进程另设 `CREATE_NO_WINDOW` 和隐藏启动信息，补齐无控制台环境的标准句柄，保留二进制导出及捕获输出。失败补偿在同一维护锁内核对原容器、镜像和挂载，确认依赖可用后仅恢复本次备份会停止、且原本运行的后端与 Agent；PostgreSQL 或前端被外部停止时保留停止状态并报告失败，不反向覆盖操作者。失败备份仍返回失败，不更新成功标记。Docker 不可用或主机断电时不能保证自动恢复。

[修复后一次实际计划任务](docs/evidence/optimization-20261026/windows-silent-task-actual-result.json)退出码 0，备份与恢复阶段约 48 秒；108 个子进程均设置无窗口标志，161 次对任务进程的采样未观察到可见窗口。原容器状态、备份清单和近期成功心跳另见[核对记录](docs/evidence/optimization-20261026/windows-silent-backup-validation.json)。两次耗时的运行条件不同，不据此计算性能提升。

[私有文件 ACL 收尾](docs/evidence/optimization-20261026/windows-private-acl-result.json)覆盖 `backups/`、`.codex-optimization/`、`deploy/runtime/` 三个私有包和 `backend/.env` 单文件：原 377 个对象仅允许当前用户、SYSTEM、Administrators，309 份文件约 7 GiB 的内容哈希、所有者及创建标识未变。原 SDDL、精确清单和前后哈希保留在本机私有审计目录，公开摘要不含凭据。继承 canary、父代理独立权限核对及真实 PowerShell 5.1 无窗口预检均通过。首次权限接口和模块加载失败保留；没有接管所有权、提权或整仓库修改 ACL。

Windows 定时备份仍使用 `tools/windows_backup_runner.pyw`，新增入口在创建日志和调用维护逻辑前，核对本项目 `backups/` 下目录的三主体私有继承；核对失败则退出 1。只读预览为 `pwsh -NoProfile -File tools/windows_private_acl.ps1 -Mode Preview`，它只盘点，不修改 ACL。当前任务未配置外部副本，UNC/异机目标权限须独立配置并核验；原后端 Python 命令不负责设置 Windows ACL，文件 `mode 0600` 也不能代替 NTFS 权限。上述 09:10 完整静默任务早于这个新入口：新入口只有三项定向测试、实际无窗口预检和继承证据，按本轮范围没有再触发完整备份或任务。

本次 ACL 减少枚举私有文件的直接读写，仍依赖可信的工作区、当前账号和管理员。执行源码的 ACL 在本次四个目标之外，仍观察到普通本机用户修改权限；因此不能声称已抵抗恶意本机用户修改代码后影响计划任务，也不是磁盘加密、当前账号失陷防护或多用户强隔离。需要更强隔离时，应把任务入口及完整调用链发布到受保护的独立执行目录，再单独审查任务迁移和访问策略。

新安装仍先使用原安装器；现有任务用下列迁移工具预览，再显式仅更新 Action。它保留原用户、有限权限、触发器、备份路径及参数，不保存密码。后台 stdout、stderr、子进程退出码和诊断保存在实际备份根目录的 `task-logs/silent-backup-*.log` 与同名 JSON；当前任务结果也可在 Windows 任务计划程序或 `Get-ScheduledTaskInfo` 中查看。不要只读取旧的成功标记判断最近一次尝试。

```powershell
pwsh -File tools/configure_silent_backup_task.ps1
pwsh -File tools/configure_silent_backup_task.ps1 -UpdateExistingAction
Get-ScheduledTaskInfo -TaskName TripPlanner-AutomatedBackup
Get-ChildItem backups/automated-daily/task-logs | Sort-Object LastWriteTime -Descending
```

日常 Prometheus 位于 `127.0.0.1:19090`。[告警实测](docs/evidence/optimization-20261026/daily-alert-delivery-result-2.json)通过 Alertmanager API 注入单个合成告警，实际本地持久收件箱收到 firing/resolved，重复事件被去重；这不代替规则触发或真实值班人员送达证明。收件箱使用[单独的固定镜像与构建配方](docs/evidence/optimization-20261026/daily-receiver-provenance.json)，本次扫描原始 High/Critical 为 0，没有沿用 Agent 的临时风险例外。

[当前 Agent 现场门禁](docs/evidence/optimization-20261026/daily-agent-security-receipt-a454bbd.json)保持所有批准的包、ELF、应用指纹和实际进程约束。Windows 原目录的 `0:0/0777` 是 Docker Desktop 反射元数据；只对两条原始精确绑定路径兼容，仍验证 UID 1000 的独占写入、fsync 和读回，不能将其当作 Linux 所有权隔离证明。

批准镜像原本包含 12 个未进入 Git 的历史评测输出。为复现这个既有实物，CI 在构建前从[明确归档的历史输入](docs/evidence/optimization-20261026/historical-build-inputs/README.md)按原始字节及批准应用哈希精确恢复；它们不作为本轮新成绩。允许指纹没有刷新，系统包、原生库或其他应用内容漂移仍会阻断。GitHub 的 [CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml) 与[安全门禁](https://github.com/W205614/langchain-trip-planner/actions/workflows/security.yml)合计 16 个展开任务，须核对当前确切提交，不能复用旧提交的绿灯。

完整功能与源码交接见[54 项功能问答账本](docs/evidence/optimization-20261026/guide-handoff.json)；每项含主问、至少两条追问、答案要点及证据边界。源码存在和本轮定向验收不等同全部功能新测，更不构成个人贡献、线上用户或准确率证明。

Agent 的后续 CI 重建曾被[基础镜像漂移检查](docs/evidence/optimization-20261026/ci-agent-native-drift-40436685294.json)阻断：上游浮动标签从 CPython 3.11.17 变为 3.11.18，87 个系统包和 135 个应用指纹一致，但 12 个 CPython ELF 字节改变。CI 现在显式使用此前已完整核验的 `python@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377` 重建已批准组合，允许指纹未刷新。第三轮实际通过全部受审组件核对和新鲜扫描，原始 High/Critical 为 59 条，其中 46 条按同一批八项未修复 CVE 的限时授权处理；早先日常镜像的 79/52 条是另一份扫描快照，不能据此推断漏洞减少。每个替换提交仍须重新核对。此固定用于复现现有受控演示产物，不代表修复八项 CVE，或已审查并接受新解释器组合。
