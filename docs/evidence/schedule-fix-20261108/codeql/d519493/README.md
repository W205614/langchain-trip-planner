# 日期控件同步修复的 CodeQL 复核草案

源码提交：`d519493e5848dafec119ccab19bbf69ffbdca6d1`。官方 Security run：[42456858599](https://github.com/W205614/langchain-trip-planner/actions/runs/42456858599)。

四语言官方 CodeQL 2.27.4 分析和原始产物上传均成功。首轮四个上下文门禁因 frontend 源码树变化而失败，Secrets job 也在活动策略绑定单测处失败，未执行扫描；没有将该工作流计为通过。策略激活后的最终工作流状态另行核对。

| 语言 | 原始 High/Critical | 新增或变化 |
|---|---:|---:|
| Java | 21 | 0 |
| Python | 2 | 0 |
| JavaScript | 3 | 0 |
| Actions | 0 | 0 |

逐项比较完整原始结果、完整规则目录、分析器／查询包版本、实际解析规则、告警位置、严重级别及顺序语义流，均与 2f0a40c 复核基线一致。工具元数据仅有 26 个查询包位置 URI 的 runner 临时 UUID 不同，逐项确认包路径、版本及其余字段一致，差异完整记录在 `comparison.json`；没有改写原始 SARIF。13 个主源码和 57 个支持源码的提交及工作区 LF 字节一致。五个受绑定目录中仅 `frontend/e2e/improvement-http.spec.ts` 变化：等待旧日期弹层关闭、筛除退出动画、确认唯一活动弹层并核对输入值；生产代码及接口、权限、依赖和数据库均未变。未扩大任何忽略规则。

原 9bb7c6a CI 浏览器超时及 trace 因果证据保留；修复前后本地完整用例各三次通过是有限样本，不将原故障声称为本地稳定复现，也不声称覆盖所有时序。

所有 ZIP 与 GitHub artifact SHA-256 一致。提交来源由 artifact.workflow_run.head_sha 和成功分析／上传步骤确认，SARIF 本身没有 versionControlProvenance。详细比较见 `comparison.json`、逐项解释见 `source-review.json`。

主代理已独立比对四份完整结果及除 runner UUID 外的完整工具定义，严格 SARIF 门禁通过后激活 `policy-draft.json`，见 [independent-review.json](independent-review.json)。此复核不执行发布、部署、真实供应商调用或功能验收；最终发布须等待最终提交的完整 CI 和 Security 成功。
