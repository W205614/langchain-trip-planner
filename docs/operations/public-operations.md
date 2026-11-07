# 公开运营、配额与个人数据

公开运营新增的接口由 Java 持有业务事实，沿用现有 Cookie/CSRF、Bearer、管理员 MFA 和审计。`/admin/operations` 提供用户、配额、反馈、邮件投递和数据作业管理；普通账号不能调用 `/api/admin/**`。

## 用户与配额

`PUT /api/admin/operations/users/{id}/state` 接受 `state=active|disabled` 和处理原因 `reason`。停用撤销所有旧凭证并停止账号任务，启用不会恢复旧凭证；正在注销或已注销的账号不能重新启用。最后一个满足当前 MFA 要求的可用管理员不能被停用或注销；最后一个活动管理员同样受保护，包括尚未设置 MFA 的初始管理员。

`GET/PUT /api/admin/operations/users/{id}/quotas` 管理 `daily_tasks`、`active_tasks`、`daily_text`、`daily_embedding`、`daily_amap`，PUT 要求提交全部五项，`null` 表示继承部署默认值，0拒绝新操作。规划任务默认每日50、同时活动4；外部调用默认每用户每日文本/视觉100、向量化500、地图1000，通过 `OUTBOUND_USER_DAILY_TEXT_LIMIT`、`OUTBOUND_USER_DAILY_EMBEDDING_LIMIT`、`OUTBOUND_USER_DAILY_AMAP_LIMIT` 配置。以上日期均按 UTC 划分。

`GET /api/usage/limits` 返回当前有效配额、覆盖值、任务数量以及各类发送前预留。预留失败会在同一事务回滚；发送结果未知时不退款。视觉请求共享text配额；任务原有供应商报告用量及费用估算继续通过 `/api/usage/summary` 查看，实际费用以供应商账单为准。

`supplier_usage` 单独汇总本UTC日预留中已经收到的供应商报告，并列出尚未报告数量、无任务调用及执行调用数量；尚未收到的报告保持未知。Python在收到非流式文本／视觉或Embedding响应后，通过内部鉴权接口 `POST /internal/v2/outbound/report` 提交原预留的 `request_key`、`run_id` 和非负整数 `input_tokens`、`output_tokens`。单次报告输入上限2,000,000、输出上限1,000,000 Token；同一预留同值重放成功，异值冲突409，不存在404。报告允许在任务取消或截止之后补到，仅记账而不恢复结果。规划流式用量另在任务报告中体现；两处描述同一次调用时只计一次，真实费用以供应商账单为准。

Java 为无规划任务的能力调用登记短时 `outbound_operations`，Python 只能回传内部请求携带的 `operation_id`。用户归属来自登录或数据库原记录，不能从浏览器／Python任意指定。公开部署启用 `OUTBOUND_REQUIRE_ACTOR_CONTEXT=true`；地图上下文只能预留地图调用，不能作为模型授权。

## 反馈

用户在 `/feedback` 提交并查看反馈。`POST /api/feedback` 接受 `category=planning|account|knowledge|other`、主题、描述及可选本人 `trip_id`、`task_id`、`request_id`，每小时最多10条。没有本人所有权的关联记录会拒绝。

管理员通过 `/api/admin/operations/feedback` 筛选，`PUT /api/admin/operations/feedback/{id}` 以 `If-Match` 更新版本、`status=open|processing|closed` 和回复。关闭必须填写回复；并发更新冲突返回409。

## 导出

用户在 `/account/data` 通过 `POST /api/account/exports` 创建持久化作业，`GET /api/account/data-jobs` 查看进度，完成后经 `/api/account/exports/{id}/download` 下载 ZIP。身份、文件所有权及有效期在每次下载时核对。

ZIP 包含本人账号公开资料、偏好、行程与版本、收藏、对话、任务输入及已报告用量、反馈、本人行程的事项与费用、投稿元数据及原文件；不导出密码、恢复码、TOTP、邮箱令牌、分享凭据、存储地址或他人私有行程。数据库内容使用同一可重复读快照，原文件按长度和 SHA-256 校验，内容变化时失败而不交付部分结果。

`ACCOUNT_EXPORT_DIR` 默认为上传卷下的 `account-exports`，必须是 Java 独占、持久化的私有目录，不映射为 Nginx 静态目录。`ACCOUNT_EXPORT_MAX_BYTES` 默认512MiB（原文件与JSON总计）。导出完成后24小时过期，维护轮询删除文件；已过期文件立即不能下载。每小时最多申请3次，已有活动导出会复用同一作业。

## 注销

`GET /api/account/erasure-preview` 展示影响；`POST /api/account/erasure` 要求当前密码、已启用时的TOTP以及 `confirmation=DELETE MY ACCOUNT`。接受后立即撤销登录、成员和分享访问、撤回公开卡片及投稿，并返回一次显示的随机注销凭据。浏览器下载凭据后进入 `/account/erasure-status`，可在30天内查询。

注销状态接口 `GET /api/auth/erasure/{id}` 必须携带 `X-Erasure-Receipt`，数据库只存该凭据的哈希；响应仅包含作业编号、状态、阶段和错误码，不公开账户信息。凭据不放在URL。

持久化后台作业删除本人行程及其版本、私人对话、收藏、偏好等，等待历史／资料索引删除作业成功和原文件清理确认，再清理未提交上传、导出文件及账号安全资料。账号行保留随机匿名名称作为他人行程中费用、审核等必要记录的外键；旧用户名、邮箱、密码、TOTP、恢复码和反馈正文不保留。必要任务身份及单调预算账本保留，但任务输入和请求标识清空。数据库状态只有在上述清理成功后才变为 `deleted`，注销作业才变为 `succeeded`。

外部索引或存储不可用时保持处理中或失败，管理员从“数据作业”查看错误并在修复依赖后重放。`GET /api/admin/operations/data-jobs` 查看最近100个作业，`POST /api/admin/operations/data-jobs/{id}/replay` 仅接受失败状态。注销不会删除当前任务之外的工作区文件。

上传完成、知识解析／发布、社区投稿／复制及导出文件发布在实际提交事务内锁定并检查账号状态，与停用／注销串行。已开始的文件传输在撤权后不能创建新的资料记录；注销等待该上传退出后再清理预留原件。知识编辑也锁定投稿人，避免注销目标版本被并发编辑越过。导出的文件读取和压缩在锁外进行，仅最终原子重命名及成功状态提交持有用户共享锁。

备份内的旧副本按备份保留期限处理，在线注销后仍会保留到期。恢复备份时须重放后续注销记录，避免重新公开已撤回内容。

## 检查

Java集成测试使用专用 PostgreSQL，覆盖停用及最后管理员保护、并发额度、任务入口与无任务调用归属、反馈所有权和版本、真实原文件导出及索引确认前后注销状态：

```powershell
$env:TEST_DATABASE_URL='jdbc:postgresql://localhost:15439/trip'
cd business-backend
.\mvnw.cmd -B -ntp '-Dtest=PublicOperationsIntegrationTest,AccountRevocationRaceIntegrationTest,OutboxWorkerTest' test
```

测试库必须为隔离验证数据，不能把日常数据库连接填入这里。`WORKERS_ENABLED=false` 时不会自动推进数据作业；测试显式调用实际处理服务。生产后台要求唯一调度所有者，失锁后停止领取作业。

2026-11-07 的撤权交错复核先在旧实现复现晚写及目标版本不匹配，再验证修复，以上定向测试合计21项通过；其中新增8项通过真实 PostgreSQL 锁与受控文件／模型 I/O 交错覆盖提交时序，见 [回归证据](../evidence/public-operations-20261107/revocation-races.json)。
