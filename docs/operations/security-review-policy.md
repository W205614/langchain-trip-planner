# 安全复核与有效期清点

2026-10-30 检查范围：当前 GitHub CI/安全工作流、Docker/Compose 构建及部署配置、Python/Java/前端运行源码、安全工具和安装脚本。历史审查材料作为证据保留。

当前构建使用的四份安全复核配置均按组件或代码变化复核，不再使用固定日历截止日：

2026-11-07 的当前绑定使用源码 `9165c2e` 的四语言新 SARIF，26项原始高危告警逐项核对查询、位置、严重等级、完整结果和语义数据流，47份支持源码与f74复核完全一致，移动端导航测试的一行变更已核对，见[最终复核记录](../evidence/personal-closure-20261107/codeql/9165c2e/README.md)。原始告警保留，当前提交的 CI 结果在对应工作流查看。

2026-10-31 源码 `2368aaf` 的22项告警及当时的依赖复核保留在[历史记录](../evidence/single-host-20261031/codeql-source-review.json)。

| 当前配置 | 必须匹配的依据 |
|---|---|
| [两库包锁](../../backend/runtime-security-packages.json) | 基座、架构、签名、精确包版本、SHA256、SONAME、APT 模拟与安装后的包变更范围 |
| [供应商适用性](../../.github/security-vendor-remediated.json) | 绑定源码、实际镜像身份、包版本、相关 ELF 字节及组件适用性 |
| [CodeQL 上下文](../../.github/codeql-context-remediated.json) | 源码树、源码哈希、查询、位置、严重等级、原始报告及语义数据流 |
| [Chroma VEX](../../.github/security-vex-remediated.json) | 精确组件版本、已审漏洞 ID、绑定源码、嵌入式客户端及执行路径；镜像还须匹配 Agent 默认入口和当前源码版本标签 |

Chroma VEX 的四项结论绑定本项目嵌入式客户端的适用性，保留相关客户端设置、HTTP 路由隔离和无网络读写回归。新增漏洞、版本变化或相关源码／入口变化后，需重新复核适用性再更新指纹。

每次 CI 继续实际扫描并保留原始结果。新增高危/严重漏洞、未评估的阻断级别告警、扫描失败、缺失结果或证据不匹配继续阻断。日历规则取消不改变扫描阈值、签名/哈希要求或既有权限隔离。

## 其他时间检查的处理

| 类别 | 清点结论与处理 |
|---|---|
| 旧风险授权、旧供应商/CodeQL/VEX、旧 source-equivalence 记录 | 保留原始期限和冻结内容。当前工作流使用已审的源码／数据流绑定配置，不传入旧风险接受或源码等价模式；旧格式验证仍拒绝过期授权。 |
| 登录令牌、MFA 待确认、分享链接及提案/澄清有效期 | 保留；控制凭据与用户操作的有效范围，不是项目使用截止日。 |
| 路线、天气、营业、预约等事实，缓存、执行检查点和时间轴绑定 | 保留；外部事实会变化，过期数据不能继续充当当前可行性的证据。 |
| 请求/任务总超时、熔断和锁等待期限 | 保留；限制实际等待与资源占用，不以固定年月日停止程序。 |
| 扫描结果与漏洞数据库的新鲜度 | 保留；使用当前漏洞数据库扫描当前组件，结果按提交存档。 |
| 测试行程日期、历史评测文件名、构建 SOURCE_DATE_EPOCH，以及 reviewed_on/assessment_date/verified_date | 数据、可重复构建或审计元信息；未发现作为当前构建或正常启动的固定日历截止检查。CI 的合成事实有效期由运行时生成。 |

本轮未发现其他会仅因某个固定日历日期到来而阻断当前构建或正常启动的项目自设限制。这个结论限于上述源码和实际入口；业务有效期及旧授权中的日期仍会在对应场景生效。

`tools/tests/test_active_security_reviews.py` 从当前安全工作流读取实际选用的复核配置，以模拟 2040 年的日期执行加载，防止当前入口无意中重新接入按日期过期的旧记录。单项反例测试继续验证源码、版本、漏洞 ID 和数据流变化不能沿用旧结论。

## 2026-10-31 安全复核记录

本轮新扫描发现并修复了前端依赖告警：`follow-redirects` 1.16.2、Vite 6.4.5，以及从依赖图移除受影响 minimatch 的 vue-tsc 3.3.14；[原始失败与修复结果](../evidence/single-host-20261031/dependency-remediation.json)单独保留，官方 npm registry 的完整审计为0项。版本来自上游修复，不增加安全例外。

glibc 的 `CVE-2026-84243` LANGUAGE 路径穿越由新构建中的 `+trip2` 源码回补处理，保留原有两项 glibc 修复。补丁来自经过审查的上游 v7 提案，使用本项目构建的回补包；构建须通过同一上游回归、ABI 检查及实际镜像扫描，包版本和 ELF 指纹精确绑定，原始告警继续保留。[修复与反例证据](../evidence/native-gettext-20261031/README.md)单独记录。这里记录当时的镜像构建与检查，日常部署状态见[单机运行手册](single-host.md)。

源码变化后须使用新SARIF重新复核精确查询、位置、源码和数据流；旧绿灯不沿用到新源码树。最终提交的CI与安全检查仍须全部实际成功。

安全门禁同时保留原始告警与有证据的适用性评估。Chroma 的四项上游问题仅在本项目显式嵌入式客户端、入口和源码指纹均匹配时适用[当前评估](../../.github/security-vex-remediated.json)。原 8 项原生 CVE 中，6 项采用上游安全代码回补；PBDS 头文件问题仅对实际匹配的 Debian 运行时包作组件不包含判定，2.42 才引入的 util-linux 子目录路径不适用于当前 2.41 分支。预编译 Python wheel 未在此流程中全部重编，共享库更新与已内联模板分别检查。

Python 依赖同时固定 LangGraph `1.2.14` 与 SDK `0.4.6`，处理[上游 v3 流式标识符路径编码漏洞](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-3fx2-cqw9-xr3c)；锁文件通过原有 uv `0.13.5` 重新生成，Python 3.11 的 Linux/Windows 有效依赖只变化这两个包。

新镜像使用[安全回补及组件适用性记录](../../.github/security-vendor-remediated.json)，核对实际包版本、相关 ELF 字节和绑定源码，并保留完整原始扫描；未知高危、扫描错误或不匹配仍阻断。CI 不再传入旧的 `--risk-acceptance` 或 `restore-response-v1` 参数。[原始授权](../../.github/security-risk-authorization.json)、[冻结指纹](../../.github/security-risk-acceptance.json)和[旧部署矩阵](../evidence/optimization-20261026/unresolved-cve-deployment-matrix.md)保留为历史证据，不是本轮组件更新的放行票据。历史两文件授权测试在对应 Git 快照上运行，不要求未来业务源码永远等于旧快照。

Agent 的日常及独立生产配置显式丢弃所有 Linux capabilities，并禁止通过 SUID/file capabilities 获得新权限；镜像精确移除无业务用途的 mount/umount/nsenter/unshare 及已核别名/副本，保留系统依赖库。根文件系统设为只读，runtime、Chroma 使用原有持久挂载，临时目录与应用文件日志分别使用 128 MiB/64 MiB tmpfs；应用文件日志随容器停止消失，控制台日志仍由 Docker 按既定容量轮转。这些约束缩小提权与写入面，不修复进程内原生库的越界写或 UAF，实际 uvicorn 候选验收结果须与对应镜像核对。旧 Bearer 凭证继续遵守签发时的 `exp` 与 `token_version`；新会话的较短到期配置不会自动撤销全部历史凭证。

Agent 先安装既有 `libacl1` 和 `zlib1g` 官方安全版本，再安装基于稳定源构建的 glibc、libstdc++ 与 util-linux 回补包；APT 按真实依赖顺序安装，最终镜像保留包管理记录，移除编译器、开发头文件及临时 sid 源。原生运行回归覆盖越界写反例、旧解析器输出和实际消费者加载。当前[两库包锁](../../backend/runtime-security-packages.json)、[供应商适用性](../../.github/security-vendor-remediated.json)和 [CodeQL 上下文复核](../../.github/codeql-context-remediated.json)取消固定日历到期，按相关源码、基座、包版本、补丁、镜像文件或告警数据流变化触发重新复核。签名、SHA256、包变更范围、实际镜像和原始告警校验继续保留；每次 CI 仍真实扫描，新增高危／严重漏洞、未评估的阻断级别告警或不匹配继续阻断。旧临时风险接受文件及其期限仅作为历史证据保留，不再参与当前放行；[Chroma VEX](../../.github/security-vex-remediated.json)也取消固定日历到期，继续绑定精确版本、漏洞 ID、源码与嵌入式执行路径。当前四份复核配置均按变化重新审查；完整清点及保留的业务有效期、历史授权和扫描新鲜度要求见[安全复核与有效期清点](../operations/security-review-policy.md)。监控派生镜像仍固定官方源码/发布物及必要依赖修补。

本轮用户暂未提供独立异机备份目标，真实异机恢复验收已明确暂缓。首次离线留出原始结果保留为 16/17，另附身份表示评分假设的审计；不将事后解释或同样本复测改写成新的留出成绩。
