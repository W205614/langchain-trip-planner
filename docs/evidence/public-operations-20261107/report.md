# 公开运营与 OpenAI 接入验收

日期：2026-11-07。基线：`36cbc12`，最终业务源码检查点：`b07c9d5`，位于未推送的本地分支 `codex/public-operations-openai`。适用范围为单机产品，不含真实预订支付、多实例高可用或云厂商交付承诺。

## 交付范围

| 能力 | 实现与证据范围 |
|---|---|
| OpenAI 文本、视觉、Embedding | 官方端点；实际完成规划、改排、图像提取、发布入库、查询和流式协议检查 |
| 账号与邮件 | 验证、绑定、重发、保留 MFA 的找回密码、加密事务发件箱及有界重试 |
| 运营与数据 | 用户停用、配额、反馈、导出、注销与回执；管理员 MFA、最后管理员保护和审计 |
| 原文件 | Local/S3 适配、归属与来源身份、迁移和删除；真实 S3Mock SDK/HTTP/成套恢复 |
| 入口与告警 | 真实本地 CA HTTPS、可信代理、Cookie/CSRF/限流；原生 Alertmanager 双渠道触发、恢复和发送失败 |
| 备份恢复 | PostgreSQL、原件、Agent 数据和私有配置；restic 加密归档及隔离恢复 |
| 接口与接入手册 | OpenAPI 3.1 的 35 个操作、生成的内部 Schema、前端类型、配置模板及联调命令 |

手册：[模型与索引](../../operations/openai-migration.md)、[邮箱](../../operations/email-accounts.md)、[运营与数据](../../operations/public-operations.md)、[对象存储](../../operations/object-storage.md)、[公开部署](../../operations/public-deployment.md)、[API 契约](../../../contracts/README.md)。

## 真实模型抽样

文本及图像模型为 `gpt-6.1-sol`，`reasoning_effort=low`；Embedding 为 `text-embedding-3-large`、3072 维。三个端点均为 `https://api.openai.com/v1`，使用用户此前指定的私有项目凭据。测试不公布凭据、账号令牌或邮件找回链接。

持久预算最终计数见同目录机器报告；文本 5/12、视觉 1/1、Embedding 10/40、高德 MCP 工具 62/120。失败预留不重置、不退款；MCP 工具内部不可观测的请求数不等于工具计数。

一页合成图像提取后经人工核对再发布，保留“合成资料、非官方旅游来源”的声明。规划首次暴露旧协议执行身份缺失，发送前被账本拒绝；修复并恢复后保存成功，改排也保存成功。两个结果均为 `degraded`，存在开放时间、预约、餐饮、路线等事实缺口。改排样本景点数量仍为两个，不能声称“减少一个景点”的语义目标通过。

问答检索命中了已发布的合成资料；生成内容没有通过保守引用支持检查，最终为明确标注的 `extractive_fallback`。独立 Chat Completions 流式请求得到非空内容、`stop` 和 usage。以上证明调用与降级链路，不证明问答语义质量或行程事实已全部核实。

小样本索引重建预览为 83 个知识块、1 条行程、23355 个输入 UTF-8 字节、约 9 次 Embedding 请求；预览前后付费计数不变。该数量只属于隔离样本。日常完整索引未迁移，不能将本次入库与预览记成全量迁移通过。

## 本地验证

- Java 最终全量：最终业务源码在全新隔离库 206 项通过，无失败/跳过；撤权竞态 8 项先复现再验证修复。复用上轮验收库的一次运行被不可重置的固定批次检查正确拒绝，旧批次保留，最终另建数据库验证。
- Python 最终全量：最终业务源码的正规 Dockerfile 镜像中 488 项通过、10 项显式跳过；其中 9 项原生 ABI/安全回补检查另以受控镜像标志执行并通过，restic 的真实二进制检查另行执行。跳过不计作通过。
- 真实浏览器：最终 backend／Agent 镜像与当前前端复跑 2 项通过、0 失败/跳过/重试，涵盖注册验证、登录、找回、通知偏好、反馈、ZIP 导出、注销回执、旧 JWT 失效及刷新后的注销状态查询；实际容器镜像 ID 与本轮结果见 [最终浏览器证据](browser-release.json)，[此前一轮](browser-final.json)保留。
- 正式 OpenAPI 元 schema、31 条路由、35 个操作、52 个 schema、11 个 Java 请求 record、6 个返回白名单校验通过。
- S3：实际上传 115 字节图像，原件与内部读取 SHA-256 一致；成套快照恢复到新 Bucket/前缀/来源及新数据库；停止重启后仍可读取一致数据。
- 不确定写入：真实 PostgreSQL 与 S3Mock 验证缺失对象的预留不会被注销提前释放，晚到对象仍须删除；相关 19 项通过，其中新增 2 项。证据明确区分注入的客户端传输异常与真实 HEAD／PUT／DELETE，见 [不确定上传回归](upload-erasure-uncertainty.json)。
- restic 0.19.1：实际成套快照的 14 个清单文件与原件字节恢复一致；错误密码被拒绝。本地仓库检查不是异机灾备证明。
- 私有配置补充快照：有效环境及额外 5 个私有配置文件已归档，11 个清单文件验证通过；原始备份受本机私有 ACL 保护。
- 新部署配置：实际只读 Agent 容器使用生成配置完成启动、持久数据集标记检查与重启；运行目录是已挂载的 `/app/runtime`，见 [运行记录](generated-agent-runtime.json)。Linux 配置目录 `0700`、写入前文件 `0600` 的新建及升级断言已在 Linux 容器通过，Windows 沿用私有父目录 ACL。
- 外部 PostgreSQL：独立 TLS／mTLS 目标与本地对照库验证选库准确、错误 CA／主机名拒绝，47 表与 S3 原件成功备份并恢复至全新目标；原库和对照库未变，见 [云库备份机器证据](../cloud-postgresql-backup-20261107/report.json)。该目标仍是隔离 PostgreSQL，不是已连接真实云厂商。
- HTTPS、原生邮件/Webhook 告警和 Linux 私有配置 UID 初次验证见[独立记录](../public-edge-20261107/report.json)。最终使用更新依赖的 Caddy 2.11.9 安全派生重新验证全部链路，原始 High/Critical 为 0、未使用豁免，详见[最终入口安全验收](../caddy-security-20261107/README.md)。

## 接入与发布边界

SMTP、S3、TLS 和告警通过的是隔离目标；真实发信域、收件箱投递、云 IAM、云数据库 TLS、外部 Webhook、DNS、公网 ACME 及异机备份恢复仍需使用选定平台配置联调。应用适配器与命令已交付，不把未实际连接的云服务标记成功。

本批次没有推送 GitHub、公开部署或启用计划备份。日常七服务保持停止，日常数据没有用于模型抽样；源码与私有模型配置已更新，日常旧容器不会自动加载新代码，正式启用前须按维护手册重建并正常升级。

## 安全验收

源码与完整 Git 历史的 Gitleaks 均为 0 项。依赖检查的 5 项 Chroma 原始记录按当前仅嵌入式使用路径逐项记录，未解决项为 0；npm 与实际 Maven 依赖审计无阻断项。

最终 Agent 镜像保留 85 条原始 High/Critical：4 条为精确 Chroma 上下文、14 条为发行版回补、47 条为锁定升级包、20 条为对应组件不存在；未解决阻断项为 0，没有接受仍有漏洞的风险。新增 zlib 记录经官方 Debian 源码补丁、包版本和实际 ELF 指纹核对，见 [独立包核对](zlib-exact-package-review.json)。backend、frontend、最终 Caddy 镜像原始 High/Critical 均为 0。扫描不能解读为无漏洞保证。

最终业务检查点 `b07c9d5` 使用官方 CodeQL 2.27.4 重新运行 Java、Python、JavaScript 与 Actions 的 `security-extended`，八个阶段全部完成。Java 按现有 CI 的 Maven compile 范围分析 96 个主源码，不含 51 个测试源；Python 237/237、JS/TS 75/75、workflow 2/2。原始阻断记录为 Java 21、Python 2、JS 3、Actions 0，26 项逐项绑定源码、位置、严重程度和完整语义流，未知或变化不自动通过。root 独立载入草案并核对四份原始 SARIF 后激活；最终未解决阻断为 0，门禁工具 76 项全部通过。见 [扫描与覆盖范围](codeql/b07c9d5/analysis-summary.json)、[新源码复核](codeql/b07c9d5/source-review.json)、[实际激活与门禁结果](codeql-activation.json)。

历史源码和 trace 的记录保留。后续文档、策略元数据及安全记录版本的一行测试断言未改动五个生产业务树，不宣称完整仓库与 `b07c9d5` 字节相同。汇总见 [安全机器证据](security-final.json) 与 [完整交付机器报告](report.json)；本轮没有远程 CI 运行。
