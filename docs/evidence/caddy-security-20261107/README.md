# Caddy 安全派生验收

2026-11-07，本地源码检查点 `93af3415cb7b6842402fadd878a8598294ee997b`。候选 `trip-planner-caddy:2.11.9-security1` 的实际镜像 ID、构建输入哈希、链接依赖和验收状态见 [report.json](report.json)。旧验收及旧原始扫描保留为修复前记录。

本次本地 Grype 原始 High/Critical 计数：2.11.7 官方镜像为 68，2.11.9 官方镜像为 2，最终安全派生为 0。最终仍有 3 项 Medium；没有 VEX、分发补丁排除或风险接受。原始报告在被 Git 忽略的 `.codex-public/security/caddy-final-image/`，摘要见 [image-summary.json](image-summary.json)。CI 对实际构建镜像执行相同安全门禁。

派生固定 [Caddy 2.11.9](https://github.com/caddyserver/caddy/releases/tag/v2.11.9) 官方源码、[Smallstep Crypto 0.92.0](https://github.com/smallstep/crypto/releases/tag/v0.92.0) 及证书库的[官方兼容迁移提交](https://github.com/smallstep/certificates/commit/cddde30af36e238f156bb557bfd1f11a05ffe6f9)，使用 Go JOSE v4.1.9；`x/net` 固定 0.62.0。实际二进制 [build-info.txt](build-info.txt) 已不含有漏洞的 Go JOSE v3，分别处理 [GHSA-mgcc-cww5-fp2q](https://github.com/advisories/GHSA-mgcc-cww5-fp2q) 与 [GO-2026-6860](https://pkg.go.dev/vuln/GO-2026-6860)。Smallstep 相关上游依赖的 Apache-2.0 许可证来源随报告保存。

136 个[标准模块](standard-modules.txt)与官方镜像名称一致，保留 ACME、内部 PKI 和反向代理。对应官方源码的 ACME／PKI 测试通过 24 个用例及子用例（7 个顶层测试，3 个包）。[HTTPS 实测](https-result.json)覆盖跳转、Cookie、HSTS、CSRF、内部路由隔离、代理头伪造和实际客户端限流；[告警实测](alerts-result.json)覆盖真实 Prometheus 触发／恢复、HTTPS Webhook 与 STARTTLS SMTP、投递失败计数和自动重试。

测试只运行 `trip-public-edge`，已确认其 11 个容器全部停止。使用隔离内部 CA，没有安装主机根证书；本轮未向公网 CA 申请真实域名证书。公网 DNS、证书签发与实际云平台仍按部署手册联调。
