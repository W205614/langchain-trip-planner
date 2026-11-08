# 候选、路线与恢复修复的 CodeQL 草案

源提交：`fe9807b38695d2a102dbc4437ca6888866cf5fc7`。官方 [Security run 42476327745](https://github.com/W205614/langchain-trip-planner/actions/runs/42476327745)。四语言官方 CodeQL 2.27.4 分析及原始产物上传成功；原四个上下文门禁因 `backend` 源码树变化失败，没有将该工作流计为通过。

| 语言 | 原始 High/Critical | 新增或变化 | 草案阻断 |
|---|---:|---:|---:|
| Java | 21 | 0 | 0 |
| Python | 2 | 0 | 0 |
| JavaScript | 3 | 0 | 0 |
| Actions | 0 | 0 | 0 |

逐项比较完整原始 result、实际解析规则、位置、严重度、顺序数据流、分析器与完整查询目录，均与 `d519493` 证据一致。工具元数据只有 26 个 runner 临时目录 UUID URI 变化，逐项确认查询包路径、版本及其余字段一致，见 `comparison.json`。四份原始 SARIF 未改写；每个 ZIP 的 SHA-256 均与 GitHub artifact digest 匹配，来源由官方成功分析／上传步骤及 artifact 的 `head_sha` 核实。SARIF 没有 `versionControlProvenance`，没有将其虚构为来源证明。

完整阅读并记录这批 26 个变更文件，包括独立候选、有效日窗、就近餐厅、实际餐次、数量提示、选中路线证据、延后路线恢复、历史缓存兼容、SIGTERM 退出、展示及直接回归。原 13 个主文件均不变；原 57 个支持文件中 **56 个不变，`backend/app/agent_api/main.py` 改变**。已重新审查其执行、鉴权、截止时间、取消和 fixture 分支，并更新实际 LF 字节绑定。合计绑定 87 个支持文件，逐文件依据见 `source-review.json`，提交和工作区 LF 字节已核对。没有扩大告警分类、严重度阈值或扫描器逻辑。

两处真实恢复问题分别处理：选中的未发送路线返回 `not_found` 和明确 Resume 原因，避免 Java 将未完成任务视为完成；RAG notices 在保存草稿前合并，使首次执行与 Resume 的 routes 输入内容一致。没有更新路线观测时间或有效期，没有新增自动付费重试。中间候选 `6480998`、`44925ff` 仅保存在忽略的私有调查目录，其产物不替代本轮新扫描。

实际执行的草案门禁（Python 进程 exit 0，四语言总阻断 0）：

```powershell
python tools/security_gate.py sarif --directory docs/evidence/schedule-fix-20261108/codeql/fe9807b --codeql-context docs/evidence/schedule-fix-20261108/codeql/fe9807b/policy-draft.json --reports .codex-local-quality-20261108/security-review/fe9807b/draft-gate
```

同一批原始 SARIF 使用旧根策略时 Python 进程实际 exit 2，原因仍为 `CodeQL business source tree changed: backend`，符合 fail-fast 契约。命令与进程码由 `subprocess` 直接记录，见 `validation.json`；实际草案输出见 `draft-sarif-summary.json`。

本代理参与了退出和质量展示部分实现，不能将这些部分自审称为完全独立复核。主代理已独立核对完整原始结果、全部87项源码绑定及真实门禁，再按原字节激活 `policy-draft.json`；见 [independent-review.json](independent-review.json)。此复核不启动日常容器、不部署、不调用付费服务，也不替代另行记录的真实 API 恢复验收、功能测试或最终提交的完整 CI。普通 HTTP 收件后退出已另行验证；未声称可为未完成的慢 HTTP 请求保证退出时限。
