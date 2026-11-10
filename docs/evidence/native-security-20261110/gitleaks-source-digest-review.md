# 公开源码摘要的凭据误报复核

2026-11-10，包含未提交证据的本地 Gitleaks 扫描报告 21 条 `generic-api-key`，均来自本目录两个 JSON 文件中的同一个公开源码文件摘要；历史扫描为 0。原始脱敏报告和首次失败记录保留在本地本轮证据中，没有改写为通过结果。

已逐条读取扫描器实际使用的源码导出快照，并在真实镜像 `sha256:e7c9075d385cd1333e42772214849374534d7f348e00c000331e31f8ab0a7f87` 中重新计算对应文件的原始及 LF 归一化 SHA-256。21 条结果全部等于同一文件的计算值，不是环境变量值、访问令牌或用户凭据。

文件：`/usr/local/lib/python3.13/site-packages/langchain_openai/chatgpt_oauth.py`。

摘要：`sha256:f7ccbe5fe7ab1070977c81305ee20aff84b024e21fdf3466f0fd93eec94c56da`。

沿用仓库已有的精确误报校准方式：仅允许两个指定证据文件与这一个字面摘要同时匹配，限定 `generic-api-key` 规则；没有排除目录、放行任意 SHA-256、修改默认规则或接受真实凭据风险。源码文件、摘要值或报告位置改变后必须重新核实。配置变化后的实际复扫结果另行记录，不能以本说明替代复扫。

后续加入 `codeql/8182d40/source-review.json` 后又报告两个前端源码摘要：`frontend/src/services/api.ts` 和 `frontend/src/services/auth.ts`。已同时按本地 LF 归一化文件和 Git 提交 `8182d40757c60a9c695d3d37b9bc4c41e1a830e5` 的实际 blob 重算确认。分别为 `sha256:f133a97a179955497243ef2a55dfad06f83cef66b5a80282a924f5b5e70736c3`、`sha256:f0fda988393a3704b49ad25638e48aa6212bb0b053b4a0f7bf9e6add95b1c95a`；校准同样仅覆盖这个指定文件和两个字面值，原始两条结果保留。

新 `codeql/843d242/source-review.json` 重复记录相同的两个源码摘要，已按其明确来源提交的实际 blob 再次核对；校准只增加这个确定的复核文件，不泛化到其他复核批次。

激活准确评估后，扫描报告又在活动 vendor policy 中记录 15 次上述 Python 文件摘要，在活动 CodeQL policy 中记录上述两个前端摘要。均已从扫描使用的实际导出快照逐条取值，并重新按实际容器文件或来源 Git blob 计算；相同字面值的校准仅增加这两个指定 policy 文件，原始 18 条报告保留。

其中另 1 条来自源码收集器固定的 NGINX 公共签名密钥指纹。已从实际前端镜像 `/etc/apk/keys/nginx_signing.rsa.pub` 重算 SHA-512，并与官方 Docker 构建来源、签名索引正反例校验材料核对。它是 `sha512:e09fa32f0a0eab2b879ccbbc4d0e4fb9751486eedda75e35fac65802cc9faa266425edf83e261137a2f4d16281ce2c1a5f4502930fe75154723da014214f0655`，不含私钥或访问能力。校准限定 `tools/container_sources.py` 与这个精确字面值。
