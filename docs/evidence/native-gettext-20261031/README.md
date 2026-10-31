# glibc LANGUAGE 路径穿越回补

文档提交 `670f4cb` 的[普通 CI](https://github.com/W205614/langchain-trip-planner/actions/runs/41181536572)通过；同一提交的[安全工作流](https://github.com/W205614/langchain-trip-planner/actions/runs/41181536672)新增识别 `CVE-2026-84243`，在 `libc6` 与 `libc-bin` 各保留一个 High 阻断项。[首次失败](first-ci-block.json)不改写为通过，也不通过重复扫描或未修复风险接受放行。

## 来源与修复

[GNU 公告](https://sourceware.org/cgit/glibc/plain/advisories/GLIBC-SA-2026-0025)和[Debian 跟踪](https://security-tracker.debian.org/tracker/CVE-2026-84243)说明，攻击需要可控制 LANGUAGE、选择非 C 消息 locale，并放置可读消息目录文件。这里修复库本身，不以默认 locale 作为豁免理由。

回补采用[上游 v7 提案](https://inbox.sourceware.org/libc-alpha/20261012112453.47383-1-avinal.xlvii@gmail.com/)的检查和回归源码，逐项拒绝包含路径分隔符或等于 `..` 的 LANGUAGE 条目，保留后续正常语言回退。仅为 Debian glibc 2.41 调整 Makefile 上下文并略去新版本 NEWS；[来源与字节核对](upstream-review.json)记录原始邮件、公告、稳定源码及回补 SHA-256。原 GNU 测试的版权和许可证声明完整保留。

截至 2026-10-31，Debian 原发行版包仍标记未修复；这是经过本地验证的源码回补，不声称存在官方 Debian 修复版。glibc 修订号升级为 `2.41-12+deb13u4+trip2`，保留原有 strfmon 和解析器安全修复，稳定基座与 CPython 版本不变。

## 实际验证

- 原构建运行同一上游用例，四种路径穿越比较失败；普通翻译与合法语言回退不失败。
- 干净源码重建后，同一测试明确返回 [PASS](gettext-after.test-result)。构建显式检查 PASS，不能以 unsupported 或跳过代替。
- 原 strfmon 回归和三项运行库公开导出符号比较通过，没有移除原有公开导出。
- 在原已验收 Agent 运行环境中只安装两个新 glibc 包，隔离、断网、只读执行实际原生消费者与回补回归：[8/8 通过](runtime-abi.txt)。此候选用于本地验证，不冒称已完成规范 Dockerfile 的准确提交 CI 重建。
- [验证汇总](validation.json)、[包 SHA-256](native-packages.json)及[实际安装的 ELF 指纹](image-fingerprints.json)分别保留。既有增量打包未被接受，最终包来自相同规范路径的干净源码重建。

安全评估仅认可准确的 `+trip2` 包、绑定源码和实际 ELF；旧版本、缺失或变化的 ELF、未知 CVE 均拒绝。扫描原始告警和门禁阈值保留，没有新增未修复风险接受。准确提交的完整镜像重建、扫描、四语言源码复核与整套 CI 结果还须以 GitHub Actions 为准。

本轮没有改动本机七服务的镜像引用、私有环境文件、数据挂载或运行限制，也没有启动日常容器或重新启用备份；新补丁不自动部署到现有日常容器。
