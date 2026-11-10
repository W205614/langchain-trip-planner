# 843d242 CodeQL 源码与路径复核

本轮绑定冻结工程提交 `843d242ff3ac5e4fe18ef6dbf3b2c6d3b51a59ce` 与 [Security 工作流 42818008714](https://github.com/W205614/langchain-trip-planner/actions/runs/42818008714)。四语言官方 CodeQL 分析和上传成功；原上下文门禁仍因旧 `backend` 业务树绑定失败，原始 SARIF、失败收据和日志摘录全部保留，不覆盖上一轮材料。

| 语言 | 原始发现 | 相比 8182d40 新增 |
|---|---:|---:|
| Java | 11 | 0 |
| Python | 0 | 0 |
| JavaScript / TypeScript | 0 | 0 |
| Actions | 0 | 0 |

四个 Actions ZIP 均通过 GitHub artifact digest 校验，再做受限安全解包。[provenance.json](provenance.json) 保留具体 artifact、job、源码 revision 和文件摘要；[source-review.json](source-review.json) 逐项复核全部 44 条路径、对应规则和完整结果对象。11 个 Java 原始结果均与上一轮相同，仍分别解释真实账本写入、固定状态路径在 SQL 前的返回及不同 lambda 接收者被混连的路径。

本次业务差异只有 BusyBox 配方归档入库和调用方改用同一校验字节，以及 Agent 指纹列表排序。三个 75,751 字节的原归档均匹配既定 `source.lock`；指纹值没有移除。具体证据见 [delta-review.json](delta-review.json)。Java `src`、Python `backend/app` 和前端 `src/services` 的 Git 内容与上一轮一致，实际 dispatch、服务端 actor、Spring 请求头 CSRF、浏览器 Cookie/请求拦截器没有变化。

这 11 项是用户已授权逐条复核的上下文误报，不是 11 项源码修复或风险接受。本任务没有新增平台关闭操作。新的精确 policy 已在业务工作树干净时通过 `load`，22 项位置/有序路径变更负控均拒绝匹配；四语言本地门禁复验及保留的原始数量见 [local-policy-replay.json](local-policy-replay.json)。活动 policy 的本地验证不替代后续实际 CI、目标分支平台告警查询、镜像扫描或运行验收，也不将旧工作流的失败改写为成功。

正常检索的配额消耗、过期 outbound 账本清理与新账本登记确实存在；RAG 状态可能触发惰性初始化。复核不放行新增 GET 业务修改或任意回调。这是代理源码自查，不是独立人工审计。
