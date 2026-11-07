# 邮箱账号与 SMTP 投递

邮箱能力由 Java 业务服务持有。默认 `PUBLIC_REGISTRATION_MODE=legacy`，不改变已有用户名注册与登录流程。切换为 `email_verified` 后，新用户注册需要邮箱，既有账号仍可登录并读取资料；AI、资料上传和发布要求先验证邮箱。

## 配置

在业务服务的私有运行配置中填写，不能放入前端环境或提交 Git：

```dotenv
PUBLIC_REGISTRATION_MODE=email_verified
PUBLIC_BASE_URL=https://travel.example.com
SMTP_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=replace-with-provider-account
SMTP_PASSWORD=replace-with-provider-password
SMTP_FROM=travel@example.com
```

`SMTP_SECURITY` 支持 `starttls`、`tls` 和隔离测试使用的 `none`。生产模式拒绝 `none`；465 端口的隐式 TLS 使用 `tls`。连接、读写超时均为 5 秒。服务不会在启动时自动发送邮件。供应商账号、发信地址授权、域名验证及网络放行须在供应商侧完成。

`ACCOUNT_SECURITY_ENCRYPTION_KEY` 使用既有的独立 32 字节 Base64 密钥，邮件载荷以不同于 TOTP 的 AAD 加密保存。不要因为开启邮件重新生成该密钥；数据库、私有配置与此密钥应成套保存。公开邮箱注册要求已启用 SMTP，生产 `PUBLIC_BASE_URL` 要求 HTTPS。它是完整来源地址，不能带路径、查询参数或片段。

## 用户流程

- `/account/security` 绑定或更换邮箱，需要当前密码；已开启 TOTP 时还需一次验证码。新地址验证完成前，原邮箱继续有效。
- 验证链接 30 分钟有效，仅能使用一次；相同用途的新链接使旧链接失效。每分钟最多一次、每小时最多 5 次。
- 邮件中的凭证位于 `/account/email#mode=verify&token=...` 的片段中，不随 HTTP 请求发送给代理。页面读取后移除地址栏片段，用户点击确认才提交验证。
- 登录页可通过已验证邮箱申请 15 分钟有效的重置链接。对未知、未验证及不可用账号返回相同提示。邮件找回不能关闭或绕过 TOTP；成功后撤销旧会话。
- 站内通知默认不发送邮件。用户验证邮箱后可主动开启，开启之后的新通知才进入邮件队列；关闭时取消尚未发送的通知邮件。邮件仅提示登录站内查看，不包含私有行程正文。

## 管理与失败语义

管理员在 `GET /api/admin/mail?status=failed` 查看脱敏投递元数据，通过 `POST /api/admin/mail/{id}/replay` 手动重放未过期的失败项。接口不返回收件地址、正文或令牌。管理员权限沿用 MFA 限制。

投递状态为 `pending`、`sending`、`retry`、`accepted`、`failed` 或 `cancelled`。网络发送在数据库事务外进行；SMTP 451 等临时错误按 30、60、120、240 秒有界等待，累计最多 5 次尝试。认证失败、550 等永久错误立即失败。响应丢失或进程中断可能在租约到期后再次发送，不能承诺恰好一次。

`accepted` 仅表示 SMTP 接受，不表示收件箱收到。被接受或已过期载荷会从队列表清除。禁用或注销账号时取消未发送邮件；已交给 SMTP 的邮件无法撤回。收件箱反垃圾规则、退信及供应商投递能力需真实服务单独验收。

## 隔离验收

使用专用 PostgreSQL 和 Mailpit，不发送到真实邮箱。应用 SMTP 地址指向 Mailpit；Mailpit Web 管理端口只绑定本机。测试按收件地址读取 Mailpit API 中的真实 SMTP 消息，覆盖令牌哈希、载荷加密、单次验证、过期、重发限流、MFA 找回和旧会话撤销：

```powershell
$env:TEST_DATABASE_URL='jdbc:postgresql://localhost:15439/trip'
$env:TEST_DATABASE_PASSWORD='isolated-test-only'
$env:TEST_MAILPIT_URL='http://127.0.0.1:18025'
$env:TEST_SMTP_PORT='11025'
cd business-backend
./mvnw.cmd -B -ntp '-Dtest=EmailAccountIntegrationTest,MailDeliveryServiceTest,TokenServiceTest' test
```

这些端口是本地隔离示例。实际执行须确认连接的是测试资源。Mailpit 通过不代表真实供应商的发信地址授权、TLS、收件箱送达或域名配置已经验收。
