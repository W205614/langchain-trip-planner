# 运行治理、既有数据升级与独立回退演练

本手册说明升级演练、数据集登记、调度、容量与保留策略。当前日常配置和备份状态见[单机运行手册](single-host.md)；选定云平台的接入步骤见[公开部署](public-deployment.md)。下方按日期保留历史验收记录。

## 既有数据升级与回退演练

`existing_data_rehearsal.py` 只接受 `Java/Flyway-v2`。它拒绝旧 V1 恢复路径，避免在日常 PostgreSQL 内创建数据库。默认模式只校验备份文件并输出计划，不创建容器或恢复目录。

先在维护窗口生成当前数据的成套备份，并选择全新的仓库外恢复目录。以下路径均替换为实际备份和新输出目录，历史备份状态见[备份说明](java-migration.md#备份和恢复)。

```powershell
python backend/scripts/existing_data_rehearsal.py `
  --backup E:\trip-private\fresh-backup `
  --output E:\recovery\trip-upgrade-new `
  --report .codex-optimization/existing-data-rehearsal-plan.json
```

候选镜像构建完成后，使用固定镜像 ID 在维护窗口串行执行下列命令。不要填可漂移的 `latest` 标签。恢复目录必须全新；前次资源保留时，下一次使用新的目录，不覆盖报告或恢复文件。

```powershell
$tripCandidateImageId = $env:TRIP_CANDIDATE_IMAGE_ID
if ($tripCandidateImageId -notmatch '^sha256:[a-f0-9]{64}$') {
  throw '请先设置已构建候选的固定镜像 ID'
}
python backend/scripts/existing_data_rehearsal.py `
  --backup E:\trip-private\fresh-backup `
  --output E:\recovery\trip-upgrade-new `
  --candidate-image $tripCandidateImageId `
  --daily-project langchain-trip-planner `
  --execute `
  --report .codex-optimization/existing-data-rehearsal-result.json
if ($LASTEXITCODE -ne 0) { throw '演练未通过；读取报告并保留全部资源' }
```

网络地址池不足时可分别传入 `--upgrade-subnet` 和 `--rollback-subnet`。两个 CIDR 必须互不重叠，并由恢复工具核对现有 Docker 网络；不要照抄未经核对的地址。

演练 A 在独立 PostgreSQL、独立目录与独立网络恢复原快照，验证原镜像就绪，再停止并唯一重命名基线后端容器。原表原列摘要与三个解压目录逐文件清单全部匹配后，才为克隆数据库和目录登记同一个数据集 ID；已有不同 ID 会拒绝，不覆盖标记。

正确候选启动前，额外保留两个候选负例容器：数据库 ID 错配、单独的上传目录 ID 错配。负例必须以对应身份错误非零退出；其他启动故障不算验证通过。数据库 ID、原目录内容和错配目录哨兵不得被覆盖，完整 Flyway 历史不得推进。正确候选强制 `APP_ENV=production`、`DATASET_ENROLL_NEW=false`，不使用 development 绕过。候选完成迁移后，核对原业务列内容与既有 Flyway 记录。

演练 B 从同一备份再恢复另一套全新的 PostgreSQL 和旧后端，旧后端就绪后再次核对完整原快照摘要。以此核对所选恢复点的数据回退结果。成功路径总计保留 7 个容器和 2 个网络，包括 2 个预期启动失败的负例；失败路径也保留已创建资源，并逐项记录。

A 中保留的旧后端连接的是已升级数据库，适合观察旧应用兼容性；数据回退使用 B 中独立恢复的原快照。

报告记录 A/B 各自耗时、总耗时、精确容器与网络、镜像 ID/标签中的源码修订，以及日常四服务的前后 ID、镜像、挂载与运行状态。脚本记录操作前后的状态，只对新恢复资源执行写入。演练启动禁用 workers 的 Java 与 PostgreSQL；恢复后在候选实例检查 Agent、检索、前端和实际角色流程。

## 数据集登记与部署挂载

数据库、上传目录、Agent runtime 与 Chroma 使用一致的 `DATASET_ID`，生产启动拒绝已知不匹配或未经登记的持久卷。数据集标记识别归属，不识别恢复时点；同一数据集的不同时间快照仍须成套恢复。

已有数据登记使用 `enroll_dataset.py`。检查模式仍要求写入者已停止，因为实际文件清单与数据库摘要必须稳定。先安排停写窗口，再执行登记检查。

```powershell
python backend/scripts/enroll_dataset.py `
  --project langchain-trip-planner `
  --backup E:\backups\trip-before-upgrade-FRESH `
  --runtime E:\project\langchain-trip-planner\deploy\runtime
```

只有受控窗口内，使用刚验证且与当前停写数据一致的备份，才在同样命令后追加 `--execute`。登记前检查原数据库名/用户、备份原表原列内容、三套文件卷身份和逐文件 SHA-256 清单；新增、缺失或变更的实际文件都会拒绝。仅根目录同 ID 的 `.dataset-id` 是可忽略的登记元数据，不能据此跳过其他文件。

新备份包含 PostgreSQL 数据挂载身份。旧备份缺少该字段时，报告为 `postgres_mount_identity_compared=false`，继续以原表列摘要比对，并单独执行恢复演练。登记失败时先排查实际挂载和数据归属，保留原 ID 与标记。

## 自动备份与原状态恢复

`automated_maintenance.py` 默认只检查；只有 `--execute` 才进入维护窗口。它先保存原状态，停写后备份，独立尝试恢复每个原本运行的应用服务，确认对应容器真正就绪，并把原本停止的 PostgreSQL 恢复为停止状态。任一步恢复失败，仍继续尝试其他恢复步骤，结构化报告保留错误阶段与服务，进程非零退出。

同一项目的备份、检查和调度恢复共用非阻塞跨进程锁，获取锁先于 Docker 状态读取及变更，并覆盖停写、归档、原状态恢复与成功心跳。另一个维护进程持锁时直接非零退出；进程退出由操作系统释放锁，但锁文件永久保留，不能手动删除重建来绕开锁。默认位置是当前执行账号临时目录下的 `trip-planner-maintenance-locks`，Windows 使用字节锁，Unix 使用 `flock`。多个账号共享 Docker daemon 时，统一设置可写的 `TRIP_MAINTENANCE_LOCK_DIR` 实现跨账号互斥；计划任务的 `IgnoreNew` 提供额外防重。

```powershell
python backend/scripts/automated_maintenance.py backup `
  --project langchain-trip-planner `
  --backup-root E:\backups\trip `
  --output E:\backups\trip\maintenance-inspection.json
```

维护成功要求归档有效且原运行状态恢复完成。运行状态或 readiness 恢复失败时，报告保留失败阶段，成功心跳保持原值。计划任务需显式安装、调用 `--execute` 并核对退出码；当前启用状态见[单机运行手册](single-host.md)。

`install-backup-task.ps1` 生成的实际命令包含 `--output <BackupRoot>\last-run.json`，失败报告同样落盘；`last-success.json` 只在整个维护成功后更新。安装脚本默认只展示配置，追加 `-Install` 才安装，不覆盖已有同名任务。当前交互账号、Docker Desktop 和备份目录写入能力都是实际执行前提。

默认 RPO 目标为 86400 秒、RTO 目标为 3600 秒，它们是规划目标。实际 RTO 还需计入故障发现、响应、切换和异机传输耗时。正式迁移前仍要验证当时最新的成套备份和回退依据。

## 副本位置与异机证据

可通过 `--replica-root` 配置新的目标目录。副本以全新目录写入并验证清单，不同步删除目标。UNC 的 `localhost`、回环 IP、本机名与本机 FQDN 不视为远端目标。其他 UNC 只记录“已配置远端目标、复制字节已验证”；`off_host=false`、`failure_domain_verified=false` 明确保留真实异机/独立故障域演练的外部缺口，不更新伪造的异机成功心跳。

## 调度所有权故障

```powershell
python backend/scripts/automated_maintenance.py recover-scheduler `
  --project langchain-trip-planner `
  --output E:\backups\trip\scheduler-inspection.json
```

探针固定从所选后端容器访问自身 `/readyz`，只有明确的 `SCHEDULER_OWNERSHIP_LOST` 才允许受控恢复。原本停止的后端不会自动重启。兼容参数 `--base-url` 若提供，须为所选前端实际发布端口的本机地址；任意外部 URL 或其他项目端口不能触发重启。恢复在维护窗口执行，以所选后端实际就绪为成功条件。

## 告警、容量和保留

生产 monitoring profile 包含 Prometheus、Alertmanager 与受控本地持久收件箱；`operational_alert_receiver.py` 限制请求体、记录字段、文件大小并去重。外部邮件／Webhook 通过[公开部署手册](public-deployment.md#外部告警)配置，并核对规则触发、接收端送达与去重。

Compose 为数据库、Java、Agent、前端和监控配置 CPU、内存、PID 与日志轮转上限。这些值用于限制资源占用，实际峰值容量和长期稳定性需在目标环境测量。容量调整需结合实际资源、队列、连接池和受影响链路证据。

数据库容量按独立低频采样观测：`trip_database_size_bytes` 读取当前连接库的 `pg_database_size(current_database())`，默认每 60 秒刷新；指标抓取只读缓存，不在每次 scrape 查询数据库。`DB_CAPACITY_QUERY_TIMEOUT_SECONDS` 默认 2 秒，限制在 1—5 秒，连接等待仍受既有连接池超时约束。`DB_SIZE_WARNING_BYTES` 默认 10737418240（10 GiB），必须大于零；它是部署可调预警线，不是数据库硬配额，也不暂停或删除业务数据。采样成功且不超过 180 秒的数据库大小高于预警线持续两分钟触发告警。

采样启动前、异常或数据不可用时，数据库大小为 `NaN`、`trip_database_capacity_sample_available=0`，不沿用上次健康值；`trip_database_capacity_last_success_age_seconds` 仅在成功时重置。上传目录 `FileStore` 采样与 JDBC 聚合独立，目录缺失或采样异常同样为 `NaN`、`trip_data_disk_sample_available=0`。数据库或上传采样未知、数据库样本超过 180 秒或对应指标缺失持续两分钟触发 `TripCapacitySamplingUnknown`，不能把无数据理解为健康。配置更长数据库刷新间隔时，应同时审查 180 秒告警新鲜度阈值。

`pg_database_size` 观测当前库的数据文件（含索引），卷剩余空间、WAL、临时文件、其他数据库及宿主磁盘单独监测。上传与 PostgreSQL 使用不同挂载和配额，同一物理磁盘仍共享容量与故障域。

`OperationalRetention` 分批处理过期快照和超保留期终态任务载荷，保留任务身份、状态、恢复血缘和调用预算。成功 Outbox 历史保留每个实体最新成功，失败/待处理工作与必要删除水位不按成功历史清理。载荷到期后恢复返回明确原因。数据保留不表示可以删除业务库、预算账本、审计或用户备份。

## 资源保留与验收缺口

上述恢复与演练工具不会自动删除容器、网络、镜像或恢复材料。只有协调窗口另行授权、归属与回退依据均已核对后，主任务才能按精确清单处理冗余对象；不使用宽泛 prune、`down -v` 或文件永久删除。

每次升级记录恢复耗时、Agent／前端读回、实际镜像身份和对应提交的 CI 结果。真实异机副本与故障域验证仍需选定外部目标。


## 规划、账号与运行治理改善

当前改动覆盖核验、规划有效性、多轮澄清、资料问答、上传配额和单机运行治理。验收状态逐项记录在 [0—12 范围账本](../evidence/optimization-20261026/scope-ledger.json)；未完成的浏览器、部署、依赖扫描或外部条件不能计作已验收。

- **核验保留原内容：** 地图超时、限流和连接失败只更新核验结果；明确不存在的景点进入待替换状态。替换需先选择可信候选、预览，再保存编辑草稿，锁定和已完成安排继续受服务端保护。
- **时间轴有依赖期限：** 每日排程绑定规划条件、旅行事实及其有效期。修改营业、住宿、预约或每日时间后，受影响日期显示待重新核验；旧时间不能继续用于新时段锁或完成操作。导出会复核权限并标明过期状态，过期内容继续显示待核验状态。
- **事实补全可操作：** 行程工作台可填写营业窗口、预约、住宿端点、提前到站、自理用餐和父子景点关系，注明来源和有效期后发起局部求解提案。用户声明始终标为用户提供，不能变成官方信息；不同 POI 编号也不直接证明可以独立游览。
- **助手保留澄清状态：** “轻松一点 → 第2天”接续同一个修改需求，支持取消、改意和到期；原行程仍只在预览确认后更新。
- **资料检索与问答分开：** 纯检索不发送回答模型请求；生成问答展示答案、来源和降级原因。引用校验检查原文支持，来源真实性与问题相关性仍需核对；演示和过期资料明确标记。
- **浏览器会话：** 新浏览器登录使用 HttpOnly Cookie、SameSite 与绑定会话的 CSRF 校验，保留旧 API Bearer 兼容。账号安全页支持修改密码、一次性离线恢复码和验证器 TOTP；开启或恢复时撤销旧凭证。离线恢复码只显示一次，请私下保存。邮箱验证和通知订阅已实现，按[邮件手册](email-accounts.md)配置 SMTP；短信尚未实现，真实异机演练待目标确认。
- **上传有累计上限：** 持久化预留限制频率、累计字节、待审数量与剩余磁盘空间。同用户、同内容和相同元数据才复用；不确定提交保留文件及待核对记录，不凭超时删除可能已提交的原件。管理员可检查核对结果。

公网使用必须经过 HTTPS 入口。`ACCOUNT_SECURITY_ENCRYPTION_KEY` 是独立的 32 字节 Base64 密钥，新配置自动生成；原配置升级时仅在缺失时生成，已有值不能随意更换。它须与数据库和私有环境一起备份，丢失会影响已启用的 TOTP。`ADMIN_MFA_REQUIRED=true` 时，未完成验证器设置的管理员只能先进入账号安全流程，不能跳过管理权限检查。

### 备份、告警与有界资源

容器已配置 CPU、内存、PID 与日志轮转；实际容量按目标环境测量。终态任务正文默认保留 90 天，过期快照延迟 24 小时分批清理；保留任务身份、恢复关联、预算账本、未完成作业与必要删除证据。超过保留期的任务需从当前行程发起新执行。

本机日常七服务及独立生产环境的 monitoring 配置均连接 Prometheus、Alertmanager 和有界本地持久收件箱，提供分组、去重、恢复通知与重复间隔。外部邮件／Webhook 接收渠道按[公开部署手册](public-deployment.md#外部告警)配置并验证送达。

本机 `TripPlanner-AutomatedBackup` 保持停用，当前手工恢复点见[迁移手册](java-migration.md#备份和恢复)。以下命令检查维护配置；手工备份在维护窗口执行，计划任务需单独配置：

```powershell
python backend/scripts/automated_maintenance.py backup
```

历史备份设计目标为 RPO 24 小时、RTO 1 小时，停用后不提供这些恢复目标。异机故障域需单独验证。调度器失锁后停止领取并拒绝旧执行结果，恢复脚本检查所选容器并实施受控重启；当前部署为单实例。

### 评测和安全证据

新增真实验收使用固定服务端批次和持久样本登记：模型最多 12 次、Embedding 40 次、高德 REST 120 次、Token 预留 800000，完整流程最多 4 条且开发/留出各 2 条；旧有更严格额度优先。失败和不确定发送不退额度，不能换编号、换库或清账本获得额度。该批次禁止 MCP，避免把一次工具调用当成一次 REST；浏览器地图 SDK 单列。预留用于额度控制，实际费用以供应商账单为准。

CodeQL、依赖、密钥与镜像检查按精确提交运行，保留原始告警、扫描失败信息及适用性分析。合成反例、离线 HTTP、真实浏览器、真实供应商和人工评审分开记证。[独立人工评审表](../evidence/optimization-20261026/blind-review/review.html)尚无真人评分；真实行程仍有事实缺口，人工质量评审待完成。

首次完整 CI 的[失败结果](../evidence/optimization-20261026/first-ci-d323a44-results.json)保留：SARIF 解析器补齐官方扩展规则引用后，实际报告的 24 项高危告警经[独立源码与数据流复核](../evidence/optimization-20261026/codeql-independent-source-review-24.md)。其中 22 项只对精确查询、位置、源码、数据流和到期时间作上下文分类，保留原始等级和数量；它们与当时 8 项未修复 CVE 的临时风险接受相互独立。新路径、新数据流、源码变化、未知或过期审查继续阻断，两项扫描函数命名误报仅作内部改名，不加例外。[第二轮 CI](../evidence/optimization-20261026/second-ci-5485f8a-results.json)实际验证四种语言门禁通过，原始高危 22 项、已审上下文 22 项、阻断 0 项；替换提交仍须重新验证。原始告警在 GitHub 保留，分级结果见对应工作流的门禁报告。

### 单机质量与维护收敛（2026-10-31）

- 每日普通景点候选维持4个的预算；已确认预约、必去及改排中的锁定／已完成景点优先保留，不再被普通候选截断。模型输入与校验白名单使用同一候选集。保留活动继续参与时间求解，冲突或未知会明确报告。
- 行前核验按日期、景点和已确认跨城交通列出缺失、声明过期、未确认、冲突、闭馆及无可用预约等事项，可直接定位补录。补录保存为用户声明，再执行局部重验，结合核验结果检查可行性。营业、住宿及其余补充事实均可检查来源、有效期并移除待保存项。
- 结果页的提案加载、确认、放弃及错误状态集中到 `useAssistantProposal`，提案响应与差异使用显式 TypeScript 类型。服务端仍负责权限、版本、事实依赖和范围保护。
- 已审的两份 Python 夹具、六份浏览器夹具及时间窗口前提已迁入正式测试源码，CI 与本地执行相同入口，不再改写测试检出目录或生成另一套浏览器副本。HTTP 场景仍只允许两种已审失效原因码的表示差异，双方必须明确过期、未核实，其余字段、类型及数组顺序严格相等；[回归测试](../../backend/tests/test_scenario_projection.py)继续覆盖额外差异的拒绝。

[迁移记录与单机验证](../evidence/single-host-20261031/README.md)保留测试迁移前后指纹、实际结果与限制。[旧适配工具](../evidence/single-host-20261031/retired-ci-adapters/)仅作历史审计，不是当前运行入口。历史留出结果为16/17，人工评审待完成；后续开发回归单独记录。

## 2026-10-27 至 31 日部署与权限审计

以下记录 2026-10-27 的部署、备份与权限审计。相关旧备份已于 2026-10-29 退役；当前日常配置和手工恢复点见[单机运行手册](single-host.md)。

2026-10-27 的日常切换保留原 PostgreSQL 卷和上传、Chroma、runtime 三处绑定目录，继续监听 `127.0.0.1:8380`。[切换前完整备份](../evidence/optimization-20261026/daily-fresh-backup.json)、[四镜像归档闭包](../evidence/optimization-20261026/latest-daily-image-archive-closure.json)和[最新旧版本独立恢复](../evidence/optimization-20261026/latest-daily-restore.json)均已验证。26 张原表按原字段比较，其中 24 张一致；Flyway 原记录前缀一致并追加 V11—V14，38 条过期超过 24 小时的执行检查点被保留策略清理，原件仍在备份中，详见[数据核对](../evidence/optimization-20261026/daily-post-upgrade.json)。

[日常业务读回](../evidence/optimization-20261026/daily-readback-result.json)实际验证旧 Bearer、新 Cookie、CSRF 拒绝、本人历史及版本读取、页面、HTML 导出和退出撤销。当时使用专用验收账号及公开行程副本；该副本没有时间轴，时间锁拒绝需另行验证。[首次失败与假设审计](../evidence/optimization-20261026/daily-readback-run1-adjudication.json)保留，完整规划链路另行记录。

Windows 任务 `TripPlanner-AutomatedBackup` 已注册为当前用户、有限权限、每日北京时间 03:00，禁止并行实例；[首次实际执行](../evidence/optimization-20261026/windows-backup-task-result.json)退出码 0，备份与服务恢复阶段约 231 秒，原四容器 ID、镜像和运行状态一致。任务依赖用户登录、Docker Desktop 运行及宿主机开机。备份保留工具只提出待复核候选，不自动删除历史备份，需要结合磁盘告警人工处理。

随后一次开机后的 [08:46 补执行失败](../evidence/optimization-20261026/windows-backup-20261027-startup-failure.json)，服务恢复也失败；用户随后确认容器由本人手动停止，该次任务按原状态停止记录处理。保存原始日志后，本项目按精确原 ID 恢复。当时任务改用宿主入口 `tools/windows_backup_runner.pyw`：外层仍为原 `pythonw.exe`，每个 Docker 子进程另设 `CREATE_NO_WINDOW` 和隐藏启动信息，补齐无控制台环境的标准句柄，保留二进制导出及捕获输出。失败补偿在同一维护锁内核对原容器、镜像和挂载，确认依赖可用后仅恢复本次备份会停止、且原本运行的后端与 Agent；PostgreSQL 或前端被外部停止时保留停止状态并报告失败，不反向覆盖操作者。失败备份仍返回失败，不更新成功标记。Docker 不可用或主机断电后，需要在恢复主机环境后核对服务状态。

[修复后一次实际计划任务](../evidence/optimization-20261026/windows-silent-task-actual-result.json)退出码 0，备份与恢复阶段约 48 秒；108 个子进程均设置无窗口标志，161 次对任务进程的采样未观察到可见窗口。原容器状态、备份清单和近期成功心跳另见[核对记录](../evidence/optimization-20261026/windows-silent-backup-validation.json)。两次耗时分别对应各自的运行条件。

[私有文件 ACL 收尾](../evidence/optimization-20261026/windows-private-acl-result.json)覆盖 `backups/`、`.codex-optimization/`、`deploy/runtime/` 三个私有包和 `backend/.env` 单文件：原 377 个对象仅允许当前用户、SYSTEM、Administrators，309 份文件约 7 GiB 的内容哈希、所有者及创建标识未变。原 SDDL、精确清单和前后哈希保留在本机私有审计目录，公开摘要不含凭据。继承 canary、父代理独立权限核对及真实 PowerShell 5.1 无窗口预检均通过。首次权限接口和模块加载失败保留；没有接管所有权、提权或整仓库修改 ACL。

当时 Windows 定时备份使用 `tools/windows_backup_runner.pyw`，新增入口在创建日志和调用维护逻辑前，核对本项目 `backups/` 下目录的三主体私有继承；核对失败则退出 1。只读预览为 `pwsh -NoProfile -File tools/windows_private_acl.ps1 -Mode Preview`，它只盘点，不修改 ACL。当前任务未配置外部副本，UNC/异机目标权限须独立配置并核验；原后端 Python 命令不负责设置 Windows ACL，文件 `mode 0600` 也不能代替 NTFS 权限。上述 09:10 完整静默任务早于这个新入口：新入口只有三项定向测试、实际无窗口预检和继承证据，按本轮范围没有再触发完整备份或任务。

该次 ACL 调整覆盖四个私有数据目标，执行源码未纳入其中，工作区、当前账号和管理员仍需可信。需要多用户隔离时，将任务入口及完整调用链发布到受保护的独立执行目录，再核对任务迁移和访问策略。

当前只需查看任务是否仍停用。重新启用备份须先确定新的备份目标并完成恢复验收；历史迁移工具和日志格式仍保留在源码及审计记录中。当前启用状态读取 `Get-ScheduledTask` 的 `State`，历史运行信息读取 `Get-ScheduledTaskInfo`。

```powershell
Get-ScheduledTask -TaskName TripPlanner-AutomatedBackup | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName TripPlanner-AutomatedBackup
```

日常 Prometheus 位于 `127.0.0.1:19090`。[告警实测](../evidence/optimization-20261026/daily-alert-delivery-result-2.json)通过 Alertmanager API 注入单个合成告警，实际本地持久收件箱收到 firing/resolved，重复事件被去重。规则触发和外部接收端送达分别检查。收件箱使用[单独的固定镜像与构建配方](../evidence/optimization-20261026/daily-receiver-provenance.json)，本次扫描原始 High/Critical 为 0，没有沿用 Agent 的临时风险例外。

[历史 Agent 现场门禁](../evidence/optimization-20261026/daily-agent-security-receipt-a454bbd.json)保持所有批准的包、ELF、应用指纹和实际进程约束。Windows 原目录的 `0:0/0777` 是 Docker Desktop 反射元数据；只对两条原始精确绑定路径兼容，仍验证 UID 1000 的独占写入、fsync 和读回，Linux 部署另行核对实际 UID、权限和写入测试。

批准镜像原本包含 12 个未进入 Git 的历史评测输出。为复现这个既有实物，CI 在构建前从[明确归档的历史输入](../evidence/optimization-20261026/historical-build-inputs/README.md)按原始字节及批准应用哈希精确恢复，用于复现当时的构建。这些历史输入的字节没有改写；新的源码和原生安全回补按当前记录单独核验，不自动刷新旧风险授权。GitHub 的 [CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml) 与[安全门禁](https://github.com/W205614/langchain-trip-planner/actions/workflows/security.yml)按当前确切提交查看各任务结果。

功能和源码入口见[54 项功能问答账本](../evidence/optimization-20261026/guide-handoff.json)。

Agent 的历史 CI 重建曾被[基础镜像漂移检查](../evidence/optimization-20261026/ci-agent-native-drift-40436685294.json)阻断：CPython 3.11.17 变为 3.11.18 时，12 个解释器 ELF 字节改变，因此当时固定旧镜像复现临时授权组合。59/46 条和更早的 79/52 条结果分别保留在对应历史扫描快照。本轮使用固定的 CPython 3.11.18 安全版本和稳定发行版原生回补，重新扫描实际产物，退出旧八项 CVE 的临时风险接受流程。
