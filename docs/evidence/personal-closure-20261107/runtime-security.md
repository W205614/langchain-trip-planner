# 收尾安全修复与运行验收

验收源码为 `f74d7fa8e38e7a998a5f2cdb9d17dea1f429b653`。本次修复冷构建的 zlib 基线校验，并更新运行镜像中的压缩库；机器结果见 [runtime-security.json](runtime-security.json)。

| 部分 | 实际结果 |
|---|---|
| Agent | 保留固定 Debian 安全包目标，按签名仓库更新安装前 zlib 基线；精确升级3包，新增与移除均为0，libc保持原版本 |
| PostgreSQL | 官方17.11镜像固定摘要，刷新 Alpine 签名安全包，实际 zlib 为1.4-r0 |
| Caddy、Alertmanager | 使用生成的 Go 锁文件，将 `klauspost/compress` 更新至1.20.3 |
| Prometheus | 官方3.16.0源码及界面资源，只提高 `x/net` 至0.62.0；实际二进制使用 compress1.20.3，界面主脚本与官方资源一致 |

PostgreSQL与备份客户端共用 `deploy/images/postgres.Dockerfile`。四套部署配置已贯通该镜像；独立云数据库备份的构建命令见[备份手册](../../operations/cloud-postgresql-backup.md)。RAG源码中的旧兼容供应商说明同步改为官方 Embedding 说明。

## 验证结果

- 备份、恢复及策略定向测试：53项通过；安全工具测试：76项通过。
- 最终 Agent 的 Linux ABI／原生回补测试：9项通过；默认 uvicorn 的运行约束检查通过。
- 最新 Grype 数据库构建时间为 `2026-11-07T06:18:48Z`，下载归档校验值与官方当前列表一致。Backend、Frontend、PostgreSQL、Caddy、Prometheus、Alertmanager 的实际镜像 High/Critical 均为0。Agent保留85项原始告警，4项嵌入式使用路径评估、14项发行版回补、47项固定安全升级、20项组件不包含；门禁阻断0，风险接受项0。
- [新四语言 CodeQL](codeql/f74d7fa/README.md)保留26项原始高危告警；逐项核对源码、位置、严重度及完整有序数据流后，活动策略门禁阻断0。
- 日常七服务使用新应用、数据库及监控镜像完成健康与HTTP检查，原挂载保留。数据库仍为V20、45名用户、61条行程、3份资料及1名可用MFA管理员。
- 验收后七服务全部停止，workers配置按原字节恢复。原预算账本仍为文本5/12、视觉1/1、Embedding23/40、高德62/120。

升级前与当前的手工恢复点分别保留，自动备份任务继续为 Disabled；本机私有状态文件记录当前恢复点。真实行程质量与云平台接入状态沿用[收尾记录](report.md)，本次没有新增模型生成抽样。

对应源码的远端报告来自 [Security run 42356710883](https://github.com/W205614/langchain-trip-planner/actions/runs/42356710883)。该次运行生成了新报告，并由旧源码绑定阻止放行；独立复核后，活动策略已绑定本次源码。最终提交的工作流结果以仓库 [CI](https://github.com/W205614/langchain-trip-planner/actions/workflows/ci.yml) 和[安全检查](https://github.com/W205614/langchain-trip-planner/actions/workflows/security.yml)为准。
