# 五条源码指纹误报的精确复核

来源：`e61976bd036ad75609d9e2df3947b381906db170`，工作流 `43682302592`，security-secrets artifact `14148926165`。ZIP SHA-256 与 GitHub digest 校验匹配，原始已全量脱敏的 history/tracked 报告各 5 条，按原始字节保存在 `gitleaks-*-before.json`；原始 ZIP 本地保留。

五条均由默认 `generic-api-key` 规则报告，实际值是三个公开源文件的 SHA-256：两份 CodeQL review 各记录 `frontend/src/services/api.ts`、`auth.ts` 的 Git 源码指纹，均在告警所属提交重新计算匹配；一份 Agent 执行路径 review 记录公开依赖 `langchain_openai/chatgpt_oauth.py` 的指纹，也在对应候选镜像重新读取文件计算匹配。它们不是实际凭据，也不是原始 SARIF 中的离线密钥。`review.json` 只保存路径、字段、长度与验证结论，不复制匹配值。

配置仅向现有两条复核 allowlist 增补这三个精确 receipt 文件路径。保留 `AND` 条件、原有精确字面指纹和 `generic-api-key` 规则范围；未增加任意 SHA/目录忽略、未禁扫描、未修改原始 SARIF、review 文件或历史。

校验使用仓库校验和固定的 Gitleaks 8.30.1 实际扫描：全 Git 历史 `--all` 与跟踪源码均为 0，原 5 条失败报告保留。负例同样调用真实扫描器：相同 receipt 内的不同 SHA 仍被 `generic-api-key` 阻断；相同 receipt 内模拟真实 API-key 格式的本地合成值仍被 `github-pat` 阻断；已审 SHA 出现在未授权路径时仍被阻断。正例仅精确已审指纹和精确路径通过。负例不使用已签发的服务凭据，也不调用供应商。结果见 `scanner-summary-after.json` 与 `negative-boundaries.json`。

这些是本地实际扫描结果，后续元数据提交的远端 CI 必须另行取得成功终态。
