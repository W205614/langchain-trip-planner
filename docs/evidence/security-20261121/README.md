# 2026-11-21 构建与安全复核

原始失败发生在 `8816094`。构建依赖安装把编译环境的 glibc 从 Debian `deb13u4` 更新到 `deb13u5`，旧源码下载阶段因此误用基座检查退出。`406a4fe` 将基座检查放回依赖安装之前，并继续验证所有原生源码包的 SHA-256。BusyBox 四项 wget 测试改用同一构建容器内的回环 HTTP 夹具，复验四项通过。

Nginx 升至官方 1.30.7 后，前端扫描从 23 条阻断匹配降至 14 条。PostgreSQL 17.12 的隔离 PostgreSQL 集成测试 8 项通过，备份入口相关 Python 测试 27 项通过。升级后 GitHub 镜像扫描的 47 条 PostgreSQL 版本匹配消失，剩余 14 条对应 OpenSSL 的两个 Alpine 子包。以上数字按扫描匹配条目计数。[原始 CI](https://github.com/W205614/langchain-trip-planner/actions/runs/44894916778) · [Nginx 和 BusyBox 复验](https://github.com/W205614/langchain-trip-planner/actions/runs/44897529825) · [PostgreSQL 复验](https://github.com/W205614/langchain-trip-planner/actions/runs/44898591301)

`1cb652d` 将 Agent 的 glibc 回补迁到 Debian `deb13u5`，保留 LANGUAGE 回补和三个既有强化补丁。OpenSSL 的五项既有修复迁到 Debian 3.5.9 上，并保留相关回归检查。本机探针确认运行层 APT 安装满足新基线及三项锁定安全包的版本约束。新的 OpenSSL 告警单独纳入镜像扫描与发布门禁。

[CodeQL 审查记录](codeql/source-review.json) 关联四份原始 SARIF。Java 的 11 个结果与既有审查的主源码、规则及 44 条语义数据流一致。Chroma VEX 与发行包评估仅更新经过复核的源码指纹，继续要求现有的漏洞适用性、入口、包版本和 ELF 等检查。正式应用镜像及公开使用入口仍对应 v0.1.7。
