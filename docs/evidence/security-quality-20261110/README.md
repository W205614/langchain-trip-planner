# 2026-11-10 安全与质量候选复核

这是未发布候选的阶段记录，不是新版本交付证明。现有正式版本仍为 `v0.1.3`；没有为复扫创建或覆盖 Tag、Release 或 GHCR 版本。

## 源码与验证

工程检查点为 `c1acec9d04578389184e575ac7a65729870c52fd`，变更及检查见 [PR #1](https://github.com/W205614/langchain-trip-planner/pull/1)。候选将浏览器 Cookie 的既有 CSRF 契约接入 Spring Security，内部只读能力改用鉴权 GET，写操作保留 POST；版本、分页和加密缓冲区使用有检查的算术，测试标识使用 crypto，APT 下载目录采用 `_apt` 所有的私有权限，合成管理员 CLI 不再打印返回对象。

MyBatis 依赖解析改为 3.6.0，其 JAR 内实际包含 OGNL 3.4.16；单独新增 OGNL 依赖不能替换 MyBatis 内的 shaded 副本。Agent 构建将 setuptools 固定为 84.2.1，保留原有依赖安装及 `pip check`。依据为 [OGNL 官方公告](https://github.com/orphan-oss/ognl/security/advisories/GHSA-crwc-vw2r-qr6m)和 [setuptools 官方公告](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w)。

本地实际检查：

- Java 定向单元测试 19 项通过；隔离 PostgreSQL 集成测试 27 项通过，包括缺失/错误/查询参数 CSRF 被拒绝、正确请求头成功，以及行程操作与运营回归。
- MyBatis 更新后的完整 Java 运行：199 项通过、10 项跳过。跳过项需要未启动的 Mailpit 或 S3Mock，不记为通过；远端完整 CI 结果另行核对。
- 已发布 Agent 的 Python 3.11 运行时，挂载本次跟踪源码：接口、契约和 Chroma 边界 15 项通过。换入校验过的 setuptools 84.2.1 wheel 后，`pip check` 和同一 15 项再次通过。容器网络关闭，不挂载日常数据或真实供应商凭据。
- 前端 `vue-tsc` / Vite 构建通过，仍报告既有大于 500 kB 的分块提示；没有调高阈值隐藏它。
- 维护工具 89 项测试通过。重新加载的来源绑定不能替代最终实际镜像清单、原生回归或完整 CI。

临时数据库使用独立容器、端口和 tmpfs 合成数据，结束后停止并移除。本机日常七服务保持原停止状态；本批次未调用真实模型或地图。

## CodeQL：修复与误报分列

[实际工作流 42779706244](https://github.com/W205614/langchain-trip-planner/actions/runs/42779706244) 对上述提交的四种语言完成官方分析和 SARIF 上传。各原始告警、原门禁失败及校验过的 Actions 制品来源保存在 [codeql/](codeql/)；原失败由绑定的业务源码树变化触发，没有覆盖失败报告。

| 语言 | 原始发现 | 本轮处置 |
|---|---:|---|
| Java | 11 | 逐条复核的 GET/CSRF 上下文误报，44 条完整路径；须与平台处置分别核对 |
| Python | 0 | 原来两条不再出现 |
| JavaScript/TypeScript | 0 | 原来三条不再出现 |
| Actions | 0 | 无新增发现 |

主分支原基线为 26 条。此次源码变更后 15 条不再出现，已从活动上下文评估中移除对应条目；剩余 11 条不是 11 项源码修复。所有路径及逐项理由见 [source-review.json](codeql/source-review.json)。仓库所有者已授权逐条核实后关闭可证实误报；平台仍须实际关闭并在最终默认分支重新查询。

这些 GET 只读取元数据、POI、天气、图片或验证标记。配额和 outbound operation 账本确实可能写入，不能说“GET 没有数据库副作用”；这些记录不修改行程、账号或权限。状态类精确路径会在 SQL 前返回；`UpstreamGuard` 的具体 readiness/HTTP 回调不会执行另一个实例的 streaming/task-progress 回调。此解释与 [RFC 9110 的 safe method 语义](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1)一致。该评估不放行新的 GET 业务变更、任意 CORS、恶意同站源或无限供应商消耗。

此记录为代理源码与路径自查，不是独立人工审计。活动配置继续绑定源码、语义路径、原始结果及规则；新结果、改动或不匹配不能沿用此次结论。

## 镜像与未完成条件

本轮已取得候选后端与前端的实际构建扫描，使用 2026-11-10 06:21:20 UTC 漏洞数据库：后端原始发现 142（129 Medium、13 Low），前端 3 Medium。它们的 HIGH/CRITICAL 门禁通过不表示全部级别归零。Agent 新构建、扫描及原生回归仍需实际终态。

之前对 `v0.1.3` 本地摘要的扫描使用 11 月 8 日数据库：Agent 238、后端 232、前端 3 项。两组对象和数据库均不同，不能把数量差直接称为修复数量。本地数据库更新网络超时的日志保留；候选远端扫描使用了新数据库。

前端仍报告 [BusyBox CVE-2025-60876](https://security.alpinelinux.org/vuln/CVE-2025-60876)，Alpine 对相应版本标为 possibly vulnerable，不能凭 `apk upgrade` 成功认定已修复。部分发行版中低级问题没有扫描器列出的修复版本；完整清单、适用性和可修复项仍需继续核对，不能作为误报批量关闭。

Chroma 和原生组件既有来源绑定的本次复核见 [runtime-source-review.json](runtime-source-review.json)。只复核鉴权只读路由、私有 APT 下载目录和 packaging 更新；没有扩大既有漏洞 ID/版本覆盖。Chroma 的已有执行路径不受影响评估不是上游修复，原始发现仍保留。

因此本记录不宣称“全部级别告警归零”、最终 CI 通过、新 OCI 发布、候选拉取运行或日常升级验收完成。普通历史版本和其他项目的 Packages 均保留；当前未发现本项目历史 Maven 应用包或多余正式版本。后续 README 可以准确说明已有稳定版 `v0.1.3` 的可获取路径，不能将候选写成已发布版本。
