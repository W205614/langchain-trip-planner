# 24 条 CodeQL 告警的独立源码复核

对象是 Security run `40432045888`、源码 `d323a44d6560412e6fb2890386701bdc88aea7c5` 的实际 SARIF。以下是逐条源码与实际 trace 的复核意见，**本报告本身没有安装忽略规则或准入豁免**，也没有使用 8 项 CVE 风险接受来解释 CodeQL 告警。

| 告警组 | 数量 | 复核判断与证据边界 |
|---|---:|---|
| Spring CSRF disabled | 1 | 默认过滤器关闭，但同一全局认证过滤器先对 Cookie 非安全方法做 token 绑定、恒定时间 CSRF 校验；显式 Bearer、内部服务 key 独立。既有 HTTP 反例覆盖缺失/错误 token 的 403，不能据此泛化其他项目或新增路径。 |
| GET apparent state change | 11 | 实际 trace 把不同 `UpstreamGuard.Operation.run` 的接收者合并，从普通 GET 的 available/post 连到另一个 stream lambda，再到 Assistant/Task consumer 写入。运行时这些 lambda 对象由各调用点分别传入，GET 不携带该 consumer。JSON 逐一记录全部入口及 Agent 分支。 |
| version + 1 | 5 | PostgreSQL INTEGER 的原子 `version=version+1 AND version=expected` 必须成功更新一行后，才执行 Java 响应、审计或通知中的加法。SQL 越界先失败，不会返回 Java 溢出成功值；有陈旧版本 409 反例，但没有声称已运行 Integer.MAX_VALUE 专门测试。 |
| TOTP allocation arithmetic | 1 | 当前唯一生产调用来自服务端 newSecret：20 字节 → 32 ASCII 字符 → GCM 标签 16 字节，加 nonce 12，分配固定 60 字节。不是客户端可控长度。若新增调用者需重新核对。 |
| e2e Math.random | 2 | 随机数只拼接合成用户名/幂等后缀；密码是明确的测试常量，真实 token 来自服务端。该 Playwright 文件不属于生产 Vite 入口。但测试文件本身无 fixture marker 守卫，不能把可覆盖的 E2E_BASE_URL 宣称为强制隔离。 |
| seed admin “password” logging | 1 | 实际 taint 源是 `admin_mfa_required` 布尔字段；返回/打印的是布尔、状态码、时间和常量。真实合成凭据写入受守卫的 ignored 文件，不在该打印返回值中。此结论不使凭据文件成为公开材料。 |
| chmod 0755 | 1 | 仅应用于显式 root 构建中的新临时目录，内容是公开、签名来源并精确校验哈希的 `.deb`；不是秘密目录，也没有给其他用户写权限。不能泛化到任意 chmod。 |
| tools “secrets()” name taint | 2 | 实际 source 是名为 secrets() 的函数返回值；返回字段只有 scanner/status/count/exitcode，不复制扫描到的秘密，且 Gitleaks 使用 redact=100。建议仅将内部函数改成中性名称并由新 CI 验证，不为这两条安装例外。 |

GET 分支**不是完全无副作用**：存在缓存、指标、外部调用预算记录；首次 RAG 状态查询还可能创建派生集合并执行维度探测。它们与 SARIF 声称的用户会话下 assistant-message/task-progress 写入不同。配额治理、服务初始化和测试误指向生产的边界仍保留。

详细 24 条 query、路径、行列、原始 trace 哈希、SARIF 时源码哈希、当前 LF 源码哈希、全部 GET 映射以及既有测试路径见 [JSON](codeql-independent-source-review-24.json)。本轮只读复核，没有重跑测试、调用模型或操作 Docker/宿主任务。

整数范围与越界错误依据 [PostgreSQL 17 官方文档](https://www.postgresql.org/docs/17/datatype-numeric.html)；告警意图参见 [CodeQL GET/CSRF 查询](https://codeql.github.com/codeql-query-help/java/java-csrf-unprotected-request-type/) 和 [文件权限查询](https://codeql.github.com/codeql-query-help/python/py-overly-permissive-file/)。
