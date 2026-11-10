# 公开源码摘要的凭据误报复核

2026-11-10，包含未提交证据的本地 Gitleaks 扫描报告 21 条 `generic-api-key`，均来自本目录两个 JSON 文件中的同一个公开源码文件摘要；历史扫描为 0。原始脱敏报告和首次失败记录保留在本地本轮证据中，没有改写为通过结果。

已逐条读取扫描器实际使用的源码导出快照，并在真实镜像 `sha256:e7c9075d385cd1333e42772214849374534d7f348e00c000331e31f8ab0a7f87` 中重新计算对应文件的原始及 LF 归一化 SHA-256。21 条结果全部等于同一文件的计算值，不是环境变量值、访问令牌或用户凭据。

文件：`/usr/local/lib/python3.13/site-packages/langchain_openai/chatgpt_oauth.py`。

摘要：`sha256:f7ccbe5fe7ab1070977c81305ee20aff84b024e21fdf3466f0fd93eec94c56da`。

沿用仓库已有的精确误报校准方式：仅允许两个指定证据文件与这一个字面摘要同时匹配，限定 `generic-api-key` 规则；没有排除目录、放行任意 SHA-256、修改默认规则或接受真实凭据风险。源码文件、摘要值或报告位置改变后必须重新核实。配置变化后的实际复扫结果另行记录，不能以本说明替代复扫。

后续加入 `codeql/8182d40/source-review.json` 后又报告两个前端源码摘要：`frontend/src/services/api.ts` 和 `frontend/src/services/auth.ts`。已同时按本地 LF 归一化文件和 Git 提交 `8182d40757c60a9c695d3d37b9bc4c41e1a830e5` 的实际 blob 重算确认。分别为 `sha256:f133a97a179955497243ef2a55dfad06f83cef66b5a80282a924f5b5e70736c3`、`sha256:f0fda988393a3704b49ad25638e48aa6212bb0b053b4a0f7bf9e6add95b1c95a`；校准同样仅覆盖这个指定文件和两个字面值，原始两条结果保留。
