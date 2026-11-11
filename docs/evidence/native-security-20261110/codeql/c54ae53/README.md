# c54ae53 CodeQL 源码与完整路径复核

本轮使用 [Security 42950756096](https://github.com/W205614/langchain-trip-planner/actions/runs/42950756096) 的四份官方新分析。四语言 Analyze/upload 均成功，原上下文门禁均因旧 `deploy` 树绑定失败；原始 SARIF、门禁 JSON 和日志摘录保留，不将原 job 的失败改写成通过。

| 语言 | 原始结果 | 相比 843d242 新增 |
| --- | ---: | ---: |
| Java | 11 | 0 |
| Python | 0 | 0 |
| JavaScript / TypeScript | 0 | 0 |
| Actions | 0 | 0 |

四个 artifact ZIP 都先通过 GitHub 公布的 SHA256，再作有界安全解包。实际 checkout 是 PR merge `af570655d56b1fde0832e1d1ee8d0d8785d695ff`，其双亲为 main 与请求的 `c54ae53a8bb92c2241a598aac4047d929c07c77e`，完整 Git 树相同；没有把两个提交对象写成同一个 SHA。见 [分析来源](analyzed-tree-provenance.json) 和 [原件凭据](provenance.json)。

[逐项审查](source-review.json) 对照全部 11 个完整结果、规则与 44 条有序路径；它们和上一批原件一致，106 个支持源码指纹亦重新计算。四个完整业务目录 `backend`、`business-backend`、`frontend`、`contracts` 与 843d242 相同。五个绑定目录中只有 `deploy/images/` 的 11 个配套构建、锁和测试文件发生变化，见 [具体差异](delta-review.json)。实际 Java 回调对象、状态接口 SQL 前返回、服务端 actor、Cookie/请求头 CSRF 与前端认证路径不变。

真实查询仍可能登记或清理调用账本、消耗配额，RAG 状态存在惰性初始化；这些行为没有被称作“所有 GET 绝无副作用”。11 项按既有授权逐条判定为限定源码/语义路径的上下文误报，未将真实风险接受记成修复，也未执行新的平台关闭操作。位置和路径消息的 22 项负控均拒绝匹配，四语言全级别重放另列于 [本地重放](local-policy-replay.json)。

该审查只绑定此次实际源码树和具体结果。配套组件的原生修复、许可来源、镜像运行、真实供应商、主分支告警及最终 CI 均是各自验收项；本地精确 policy 生效不代表原工作流或最终发布通过。
