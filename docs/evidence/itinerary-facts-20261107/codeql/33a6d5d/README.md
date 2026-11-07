# MIT 源码发布候选的 CodeQL 复核

候选源码为 `33a6d5dfa47ac027955eaac0ae667e9ce31e7ff1`，四语言官方分析来自 [Security run 42373872780](https://github.com/W205614/langchain-trip-planner/actions/runs/42373872780)。[来源记录](workflow-source.json)核对 run 的目标 SHA、四个分析与上传步骤、artifact ID 和摘要；本地 ZIP 摘要及其 SARIF 成员字节均已核对。[下载凭据](download-receipts.json)保留公开 artifact 元数据，不含访问令牌。

四个分析和 SARIF 上传步骤均成功。原有上下文门禁因业务源码树变化而拒绝放行；保存来源快照时整个工作流仍在执行。本记录不能作为该工作流整体通过的证明。

## 结果与源码

| 语言 | 原始 High / Critical | 本次草案复核后未评估阻断 |
|---|---:|---:|
| Java | 21 | 0 |
| Python | 2 | 0 |
| JavaScript / TypeScript | 3 | 0 |
| Actions | 0 | 0 |

四份原始 SARIF 原样保留：[Java](java.sarif)、[Python](python.sarif)、[JavaScript](javascript.sarif)、[Actions](actions.sarif)。26 项完整结果、实际解析的完整规则、查询、位置、严重度和有序语义数据流均与 `dc1e461` 的对应官方结果一致；[逐项比对](finding-comparison.json)记录原始结果、规则与数据流哈希，未删除或降低告警。

[完整业务差异](business-source.diff)涉及八个文件：替换 PDF 渲染器及其依赖、真实 PDF 渲染与并发回归、单个测试的环境隔离，以及三处 Docker 构建上下文排除。既有 47 份支持源码、26 项告警的主源码及调用链均未变化；八个差异文件逐项复核并新增绑定，另补齐两份被完整数据流引用的未变化 Java 调用方。最终草案绑定 57 份支持文件及五个完整业务目录 Git 树，见[源码快照](source-snapshot.json)和[独立复核](source-review.json)。

PDFium 的进程内互斥锁覆盖文档创建、页面读取、渲染、PIL 缓冲区读取及所有关闭操作；PIL 图像先于 bitmap 关闭，页面和文档在异常路径也关闭，模型调用在锁外。输入大小、页数和像素上限保留。该检查不代表已证明原生 PDF 解析器不存在漏洞；新依赖和实际运行镜像仍须通过各自扫描。

## 草案门禁

在业务工作区干净且 HEAD 为上述候选提交时，实际执行：

```powershell
python tools/security_gate.py sarif --directory docs/evidence/itinerary-facts-20261107/codeql/33a6d5d --codeql-context docs/evidence/itinerary-facts-20261107/codeql/33a6d5d/policy-draft.json --reports docs/evidence/itinerary-facts-20261107/codeql/33a6d5d/draft-gate
```

退出码为 `0`，四语言均无未评估阻断项；[机器结果](draft-gate/sarif-summary.json)及[原始输出](draft-gate.log)保留。这里仅生成并验证 [policy-draft.json](policy-draft.json)，没有修改根策略或提交代码。维护者核对后才能激活；最终提交仍须重新实际运行并通过功能、安全与镜像工作流。
