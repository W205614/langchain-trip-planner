# 路线事实期限与数字边界的 CodeQL 草案

源提交：`1e555625670da110c91c8c92b8438f531e070086`。官方 [Security run 42482823055](https://github.com/W205614/langchain-trip-planner/actions/runs/42482823055)。四语言官方 CodeQL 2.27.4 分析及原始产物上传成功；原四个上下文门禁因 `backend` 源码树变化失败，没有将该工作流计为通过。

| 语言 | 原始 High/Critical | 新增或变化 | 草案阻断 |
|---|---:|---:|---:|
| Java | 21 | 0 | 0 |
| Python | 2 | 0 | 0 |
| JavaScript | 3 | 0 | 0 |
| Actions | 0 | 0 | 0 |

逐项比较完整原始 result、实际解析规则、位置、严重度、有序数据流、分析器与完整查询目录，均与当前 `fe9807b` 证据一致。工具元数据只有 26 个 runner 临时目录 UUID URI 变化；逐项确认查询包路径、版本及其余字段一致，详见 `comparison.json`。四份原始 SARIF 未改写；每个 ZIP 的 SHA-256 与 GitHub artifact digest 精确匹配。来源由官方成功分析／上传步骤及 artifact 的 `head_sha` 核实；SARIF 本身没有 `versionControlProvenance`。

原 13 个告警主文件均不变。原 87 个业务支持文件中 **84 个不变，3 个变化**：`time_planner.py`、`travel_facts.py`、`test_local_route_reliability.py`。三项均重新审阅并更新实际 LF 字节绑定；新增数字回归测试纳入绑定，合计 **88 个**。既有候选、日窗、餐饮、路线、恢复和退出源码继续受绑定。

最终 TTL 改动区分缺失事实与过期事实：**未知端点没有 provider fact**，因此不向选中事实列表添加伪造的 EPOCH 观测；端点缺口、估算交通和 `unverified` 标记仍保留。有实际路线事实时，仍使用原观测时间与原最早有效期；真实失败或未观测的无效事实仍为 EPOCH，真实过期事实仍过期。没有更新有效期、抹除失败事实或将未知接驳认证为可行。已重读真实事实生成、选中事实聚合、恢复缓存及未改动的 Java `ScheduleDependencies`／`validateProposalValidity`，没有放宽确认期限、所有权或认证检查。三条新回归分别约束上述区别；完整功能验收由主代理另行记录。

数字 POI 修复保留转义字面匹配及既有事实验证边界，只为首尾 Unicode 十进制数字增加相邻数字边界，避免景点 1/10 等被误判为父子景点。相同 ID、真实包含关系、显式 separate 及保护规则保留。中间 `66166be` 证据已整体移入忽略的私有调查目录，其未激活草案不作为本轮证据。

`tools/security_gate.py` 和下载测试另有精确源码 SHA、完整 AST 差异及真实测试证据，见 `tool-source-review.json`。**这两项不受现有 policy 自动绑定**，主代理需在最终干净副本再次核对。loader 五个业务目录范围不变。对活动基线的 AST 比较确认除 `URLError` import 外，仅 `tool()` 改变；扫描版本、SHA pins、严重度阈值、VEX/vendor assessments、结果解释和其他门禁函数未扩大。下载最多三次、单次仍 120 秒，仅对直接或包装的超时重试，完整 SHA 验证后才缓存；HTTP 错误、哈希不符和最终超时仍失败。

实际执行 `python -m unittest tools.tests.test_scanner_download -v`，5 项通过，日志保留。这是模拟下载边界，不冒充真实 CLI 下载或漏洞扫描成功。

实际草案门禁（Python 进程 exit **0**，四语言总阻断 **0**）：

```powershell
python tools/security_gate.py sarif --directory docs/evidence/schedule-fix-20261108/codeql/1e55562 --codeql-context docs/evidence/schedule-fix-20261108/codeql/1e55562/policy-draft.json --reports .codex-local-quality-20261108/security-review/1e55562/draft-gate
```

同一批原始 SARIF 使用旧根策略，实际 exit **2**：`CodeQL business source tree changed: backend`。进程码和命令见 `validation.json`，实际草案输出见 `draft-sarif-summary.json`。

主代理已独立核对四语言完整结果、88项业务绑定及两份额外工具源码，执行真实门禁通过后按原字节激活 `policy-draft.json`；见 [independent-review.json](independent-review.json)。两份工具源码不属于现有策略自动绑定范围。正式发布仍需最终提交完整 CI。本复核不启动日常容器、不部署、不调用付费模型或高德，也不替代另行记录的五服务功能、真实恢复与真实路线验收。
