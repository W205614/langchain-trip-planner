# 2026-11-22 原生运行时安全门禁复核

[Security gates 45013070478](https://github.com/W205614/langchain-trip-planner/actions/runs/45013070478) 扫描了 `89b22da` 的实际镜像。应用 Agent、Java、前端分别留下 12、9、6 条阻断匹配，五个配套镜像各留下 6 条。该次 CodeQL、依赖和秘密检查通过。阻断来自 libc 与 OpenSSL，Chroma 的四条上游告警仍按实际嵌入式路径核验。

[v0.1.7 的安全门禁](https://github.com/W205614/langchain-trip-planner/actions/runs/43683770047) 使用 11 月 14 日的漏洞库，当时八个镜像扫描均通过。使用与 `89b22da` 同一构建日期（2026-11-22）的 Grype 数据库复扫三个已发布的 v0.1.7 镜像，Java 和前端均出现本轮三项 OpenSSL 原始匹配，Agent 出现本轮三项 libc 和两项 OpenSSL 原始匹配。复扫留存原始报告，历史门禁结果保持其时间边界。

## 回补源码

- Debian libc 2.41 的 IBM1390/IBM1399 转换先检查完整输入长度，CP949 的转换游标仅在转换成功后提交，glob 的前缀拼接失败时由调用方统一释放。回补保留既有下游 TLS、栈与内存分配强化。[IBM 编码补丁](https://sourceware.org/pipermail/libc-alpha/2026-November/182107.html) · [指针边界复核](https://sourceware.org/pipermail/libc-alpha/2026-November/182116.html) · [CP949 公告](https://security-tracker.debian.org/tracker/CVE-2026-121757) · [glob 补丁](https://sourceware.org/pipermail/libc-alpha/2026-November/182067.html)
- Debian 与 Alpine 的 OpenSSL 下游构建使用三个固定的上游变更快照。QUIC 采用生产路径的判断，DANE 保留可信库替换后的证书验证，DTLS 按已接收数据逐步分配。三个上游提案在本轮取证时均处于评审阶段。[QUIC](https://github.com/openssl/openssl/pull/33544) · [DANE](https://github.com/openssl/openssl/pull/33634) · [DTLS](https://github.com/openssl/openssl/pull/33729)

Alpine 的初次工具链安装遇到下载 I/O 错误。本机以同一 Alpine 构建基座中已安装的工具链，在断网环境对固定源码和 APKBUILD 执行 abuild。主测试矩阵的汇总为 `Files=346, Tests=4291, Result: PASS`，其中 QUIC、DANE 和 DTLS 测试通过。发布候选仍需通过正常 Docker 构建、签名与最终镜像扫描。

## 候选镜像回归

- Agent 在断网容器中运行 110 项原生包、ABI 和运行时回归，全部通过。构建阶段另通过 9 项 libc 定向回归、56 项强化回归及新增的正常转换和 glob 检查，并验证了 OpenSSL 的包安装、SONAME、导出符号和 IPv4/IPv6 datagram 行为。[Agent 包和文件比较](agent-native-rebuild.json)
- GitHub 首轮候选的 Agent ABI 测试读取了新库，夹具仍要求旧摘要。新摘要已由实际 Debian 包核对，并用升级后的完整镜像重新运行该测试。初次失败留在对应作业记录里。[首次候选安全门禁](https://github.com/W205614/langchain-trip-planner/actions/runs/45019327247)
- 接收器原有 CPython 修复文件保持相同字节。新镜像只改变两个 OpenSSL APK 版本及对应的库文件和来源收据。用已更新的完整指纹重放 GitHub 实际报告的 22 条匹配，8 条对应原有 CPython 修复，14 条对应 OpenSSL 回补，未解决项 0。该重放仍需最终源码的同源 CI 确认。[接收器证据](receiver-openssl-rebind.json)
