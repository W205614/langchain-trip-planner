# 移动端导航与公开运营补验

源码检查点为 `9165c2efc832f527ae9bebe03f4384961199f54a`。移动端验收已覆盖新增的“我的数据”和“问题反馈”，九个入口在390像素视口全部可见，无横向溢出。变更为原导航测试期望数组的一行更新。

前一轮CI中的旧断言预期7个按钮，实际为9个，浏览器步骤因此失败，后续公开运营与HTTPS步骤被跳过。本轮更新后完成了以下实际补验，汇总见 [browser-navigation.json](browser-navigation.json)：

- 本机真实 Playwright 移动端导航检查：1项通过。
- 隔离环境公开运营浏览器：2项通过，跳过0、失败0；包含邮件验证、未验证用户限制、通知订阅、反馈、ZIP导出、注销撤权和密码找回。
- HTTPS与代理：跳转308，Secure／Strict会话及CSRF Cookie、HSTS、CSRF拒绝及接受、保护路由、伪造转发头、独立客户端限流检查通过。
- 真实 Prometheus／Alertmanager：邮件与Webhook的触发、恢复、503发送失败计数及重试恢复通过；SMTP STARTTLS及HTTPS Webhook校验各自本地CA。
- 两套隔离环境的供应商账本均为0；使用dummy配置，供应商进程仅连接internal网络。临时容器、卷和网络在验收后按精确归属清理，日常七服务保持原镜像并停止。

[新四语言CodeQL复核](codeql/9165c2e/README.md)的26个完整原始结果、规则描述符及47份支持源码与前一复核完全一致；活动策略完成独立门禁验证。原始报告保留，原始高危26项、门禁阻断0。

Windows Docker Desktop的纯internal网络首次未发布本机端口，该次连接失败结果保留在本机。随后为测试入口配置独立ingress桥，供应商进程继续保持internal隔离，产品信任列表未改变。
