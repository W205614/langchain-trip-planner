# 2026-11-21 构建与安全复核

原始失败发生在 `8816094`。构建依赖安装把编译环境的 glibc 从 Debian `deb13u4` 更新到 `deb13u5`，旧源码下载阶段因此误用基座检查退出。`406a4fe` 将基座检查放回依赖安装之前，并继续验证所有原生源码包的 SHA-256。BusyBox 四项 wget 测试改用同一构建容器内的回环 HTTP 夹具，复验四项通过。

Nginx 升至官方 1.30.7 后，前端扫描从 23 条阻断匹配降至 14 条。PostgreSQL 17.12 的隔离 PostgreSQL 集成测试 8 项通过，备份入口相关 Python 测试 27 项通过。升级后 GitHub 镜像扫描的 47 条 PostgreSQL 版本匹配消失，剩余 14 条对应 OpenSSL 的两个 Alpine 子包。以上数字按扫描匹配条目计数。[原始 CI](https://github.com/W205614/langchain-trip-planner/actions/runs/44894916778) · [Nginx 和 BusyBox 复验](https://github.com/W205614/langchain-trip-planner/actions/runs/44897529825) · [PostgreSQL 复验](https://github.com/W205614/langchain-trip-planner/actions/runs/44898591301)

`1cb652d` 将 Agent 的 glibc 回补迁到 Debian `deb13u5`，保留 LANGUAGE 回补和三个既有强化补丁。OpenSSL 的五项既有修复迁到 Debian 3.5.9 上，并保留相关回归检查。本机探针确认运行层 APT 安装满足新基线及三项锁定安全包的版本约束。新的 OpenSSL 告警单独纳入镜像扫描与发布门禁。

[CodeQL 审查记录](codeql/source-review.json) 关联四份原始 SARIF。Java 的 11 个结果与既有审查的主源码、规则及 44 条语义数据流一致。Chroma VEX 与发行包评估仅更新经过复核的源码指纹，继续要求现有的漏洞适用性、入口、包版本和 ELF 等检查。正式应用镜像及公开使用入口仍对应 v0.1.7。

最新构建直接执行 Debian u5 的解析器和加载器测试。Debian 已提供前置保护页的测试分配器，移除重复定义后仍执行前置、后置保护页和实际边界检查。OpenSSL 3.5.9 本机重建通过 SONAME、导出符号及运行回归，Agent 镜像回归改用重建所得的三个库文件摘要。完整镜像仍以 GitHub 构建和最终镜像检查为准。

已确认 Alpine `3.5.9-r1` 的 7 个 OpenSSL 漏洞 ID 仍触发镜像阻断。OpenSSL 3.5 分支已合入 CVE-2026-124709、CVE-2026-121482、CVE-2026-107493 和 CVE-2026-35190 的修复，当前 Alpine 稳定包尚未包含这些提交。CVE-2026-107494、CVE-2026-95690 和 CVE-2026-84786 仍待可核验的稳定分支修复。[镜像扫描](https://github.com/W205614/langchain-trip-planner/actions/runs/44900375311) · [OpenSSL 上游版本](https://github.com/openssl/openssl/releases)

预警接收器的精确匹配因两个已签名 Alpine OpenSSL 包从 `3.5.9-r0` 升至 `3.5.9-r1` 而失效。复核从 GitHub 产物读取实际 rootfs 字节，并用签名验证后的官方 APK 离线安装独立确认两个库的 SHA-256。入口、用户、架构、其余 APK 及三个 CPython 回补文件保持旧审查的精确身份。安装回补及隔离 HTTP 回归步骤通过。仅将既有三项实际回补重新绑定当前 APK 和库字节，剩余 5 条 CPython 匹配和 14 条 OpenSSL 包匹配继续阻断。[接收器构建和回归](https://github.com/W205614/langchain-trip-planner/actions/runs/44900375311/job/136834881013) · [原回补依据](../runtime-support-security-20261110/receiver-review.md)

源码 `c824d05` 进一步回补 CVE-2026-124249、CVE-2026-16052 和 CVE-2026-19552。使用固定的 Python 3.13.18 源包进行本机容器构建，六组上游测试实际通过 1,287 项，另有 103 项按上游条件跳过。三个新增模块随 Agent 和预警接收器镜像安装。接收器的门禁逐项要求这些文件的实际 SHA-256，且继续要求原有 APK、架构、运行入口、用户和 ELF 证据。剩余的 OpenSSL、IMAP 和归档命令行告警仍按完整扫描判定。[上游补丁与回归入口](../../../backend/security/cpython-backports/README.md) · [本轮 CodeQL 原始产物](https://github.com/W205614/langchain-trip-planner/actions/runs/44904164558)
