# bb9fa61 原始 CodeQL 上下文复核

- 来源：`bb9fa614873fd1779dc13f0c1c9698645999137a`，GitHub Actions run `43678971355`。
- 四语言原始 SARIF 全部保留。Java 原始高危结果 11 条、完整 codeFlows 44 条；Python、JavaScript、Actions 结果均为 0。复核不修改原始 SARIF、不关闭查询、不改变严重等级、不接受已知风险。
- 相比前次 `759c9d0`，11 个 Java result **不是字节相同**：每条结果的唯一 JSON 差异为 `/rule/index` 从 `102` 变为 `83`。两份规则目录解析出的完整 rule 对象相同，11 条语义 finding 身份及全部 44 条原始 codeFlows 完全相同。逐条新旧哈希和唯一差异见 `raw-result-differences.json`；新 `source-review.json` 和 policy 已绑定新 raw-result 指纹。
- 两个源码版本的 `business-backend` Git tree 相同。本次重新核对状态路径提前返回、`UpstreamGuard` 的具体回调接收者，以及真实地图 GET 的出站计费/过期清理记录。地图 GET 不能笼统称为无写入；这些路径没有修改行程、账户或权限，调用身份来自服务端认证请求或匿名上下文。跨站请求不会携带 `SameSite=Strict` 会话 Cookie，Cookie 认证的非安全 HTTP 方法仍由 Spring CSRF filter 检查会话绑定的 `X-CSRF-Token`。CSRF 值是会话令牌的 SHA-256 派生值；JWT 本身使用 HS256，二者不混称为 HMAC。
- 当前新增的 Python 规划/数量与攻略引用检查、检查点兼容，以及前端 DNS 配置变更单独列入 supporting source bindings。源码字节从固定 Git revision 批量读取，未把并行进行的后续依赖升级混入本次证据。

在固定 `bb9fa61` 的干净独立工作树上，实际 `codeql_context.load` 通过；四语言安全门禁均通过，Java 保留原始 11 条高危并逐条判定 `not_affected_context`、阻断 0，其他三语言阻断 0。结果见 `pinned-load.json`。相关门禁测试实际通过：12 tests、26 subtests，日志由本轮本地 `.cache/release-v017/codeql-context-tests.log` 保留。

这份复核仅适用于上述来源。后续 OpenTelemetry 等依赖修改、最终源码提交、镜像身份与新扫描结果仍需单独核对，不能把本次结果当作最终发布提交的 CI 已通过。前次证据保留在 `docs/evidence/normal-use-20261112/codeql-ui-contract-tests/`，未改写。
