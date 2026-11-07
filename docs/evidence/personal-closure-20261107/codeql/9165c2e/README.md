# 9165c2e 四语言 CodeQL 复核

源码：`9165c2efc832f527ae9bebe03f4384961199f54a`。新报告来自 [Security run 42361154555](https://github.com/W205614/langchain-trip-planner/actions/runs/42361154555)，使用 CodeQL 2.27.4。四种语言均完成分析并上传 SARIF；该次运行使用旧 `frontend` tree 绑定，四项上下文门禁失败。

本轮以 [f74 新报告](../f74d7fa/README.md) 为基准。五个业务目录的唯一变化，是响应式浏览器测试在 `expectedActions` 中加入页面已有的“我的数据”和“问题反馈”两个标签。

- [原始报告身份](artifact-provenance.json)：四份 SARIF 来自新提交对应运行，下载后校验 artifact SHA256，原始字节保留。
- [逐项源码复核](source-review.json)：Java 21、Python 2、JavaScript 3、Actions 0，共 26 项原始高危告警；47 份支持源码 LF 哈希全部未变，五个业务目录绑定新提交的实际 Git tree。
- [完整结果对照](identity-and-fullraw-comparison.json)：26 个完整原始结果对象、原始结果哈希以及完整规则描述符均逐项相同，包括位置、严重度和完整有序数据流；无需归一化即可相等。
- [源码快照清单](source-snapshot.json)和[完整业务差异](reviewed-business-diff.patch)：来自该提交的 `git archive` 与 Git diff。
- [策略草案](policy-draft.json)：保存激活前版本；当前活动策略已完成独立 `load/assess` 与四份原始报告验证。

逐项适用条件、调用路径和局限保留在源码复核记录中；该次旧绑定门禁失败与新草案复核结果分别记录。
