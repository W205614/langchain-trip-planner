# 8182d40 CodeQL 阶段复核

这份材料仅绑定工程提交 `8182d40757c60a9c695d3d37b9bc4c41e1a830e5` 与 [Security 工作流 42815262129](https://github.com/W205614/langchain-trip-planner/actions/runs/42815262129)，不是最终 CI 或发布证明。四种语言的官方 CodeQL 2.27.4 分析、SARIF 上传均成功；原上下文门禁均因 `CodeQL business source tree changed: backend` 失败。原始 SARIF、失败摘要和日志摘录保留，完整下载来源及步骤状态见 [provenance.json](provenance.json)。

| 语言 | 原始发现 | 相比上一轮新增 |
|---|---:|---:|
| Java | 11 | 0 |
| Python | 0 | 0 |
| JavaScript / TypeScript | 0 | 0 |
| Actions | 0 | 0 |

[source-review.json](source-review.json) 逐条保留 11 个原始结果、44 条完整路径的复核。它们的完整结果对象、规则与上一轮一致；并非将 11 条误报记成 11 项源码修复。用户已授权逐条核实后关闭可证实误报，本任务没有新增平台关闭操作，也不将该授权扩成真实风险接受。

与上一轮 `c1acec9` 相比，整个 Java `src`、Python `backend/app` 和前端 `src/services` 的 Git 内容未变。重新核对了实际 HTTP/readiness lambda、`UpstreamGuard.execute` 的具体对象调用、固定状态路径在 SQL 前的返回、服务端 actor 绑定、Spring 请求头 CSRF 与浏览器凭据拦截器。SQL 调用账本和配额消耗确实存在；其与检索相伴，不能被描述为“GET 完全没有副作用”。状态类 RAG 惰性初始化也保留原有边界说明。

前端改为组件本地导入，保留全局样式和 `message`/`Modal` API；直接控件边界修正保留原有空值及序列化语义，没有修改 HTTP 入口、方法或 CSRF/鉴权实现。原生运行时与镜像材料更新另有真实构建、ABI、运行时和扫描门禁，CodeQL 源码复核不替代这些检查。

候选 policy 仅保存于本机 ignored 材料，没有写入活动 `.github` 配置。后续工程变更将改变业务树，即使这些 Java 路径完全相同，也必须取得最终提交的实际新分析并重新绑定；不能以本轮结果冒充后续提交的 CI 成功。本记录是代理源码自查，不是独立人工审计。
