# f74d7fa 四语言 CodeQL 复核

源码：`f74d7fa8e38e7a998a5f2cdb9d17dea1f429b653`。原始报告来自 [Security run 42356710883](https://github.com/W205614/langchain-trip-planner/actions/runs/42356710883)，使用 CodeQL 2.27.4，四项分析及 SARIF 上传均成功。该次运行使用旧源码绑定，四项上下文门禁失败；本目录提供新源码的独立复核记录，已通过活动策略的独立门禁验证。

- [原始报告与 artifact 身份](artifact-provenance.json)：Java 21、Python 2、JavaScript 3、Actions 0，共 26 项原始高危告警，SARIF 保留下载后的原始字节。
- [逐项源码与完整数据流复核](source-review.json)：绑定 47 份支持源码及五个业务目录的 Git tree。45 份支持源码未变；两项变化分别为 RAG 的三行文档说明和 zlib 稳定仓库基线版本。
- [完整原始结果对照](identity-and-fullraw-comparison.json)：所有告警的 query、位置、严重度和有序数据流相同。新报告重排规则与文件索引，并嵌入规则帮助文本；解析实际引用后，完整结果相同。原始差异及新旧原始结果哈希逐项保留。
- [源码快照清单](source-snapshot.json)与[完整业务改动](reviewed-business-diff.patch)：快照来自该提交的 `git archive`，仅包含已提交文件。
- [策略草案](policy-draft.json)：保存激活前版本；活动策略位于 `.github/codeql-context-remediated.json`，已完成独立 `load/assess` 与四份原始报告验证。

告警保留在原始报告中。每项结论适用于所列调用路径、源码和约束，具体依据与局限见逐项记录。
