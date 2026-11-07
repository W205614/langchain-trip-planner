# 安全政策

## 报告漏洞

请使用 GitHub 的 [Report a vulnerability](https://github.com/W205614/langchain-trip-planner/security/advisories/new) 私密报告安全问题，该仓库已启用此入口。普通功能缺陷使用 Issues。

报告请包含受影响的提交或版本、部署方式、最小复现步骤、影响范围及脱敏日志。有修复建议或补丁时可一并提供。请使用自己的隔离环境和测试账号复现，公开讨论前先移除密钥、Cookie、令牌和用户数据。

如果凭据已经泄露，先在对应平台撤销并轮换，再提交脱敏报告。删除公开文本后，泄露的旧凭据仍须撤销。

## 维护范围

当前修复面向 `main` 分支，旧版本升级到当前维护版本后复验。项目由个人维护，报告进度和修复安排通过 GitHub 私密讨论沟通。

[安全工作流](.github/workflows/security.yml)检查密钥、依赖、CodeQL 和实际运行镜像；已审告警保留其适用条件，相关代码或组件变化后重新复核，规则见[安全复核手册](docs/operations/security-review-policy.md)。部署者按[公开部署手册](docs/operations/public-deployment.md)配置 HTTPS、私有凭据、最小权限及备份，并完成所选外部平台的联调。
