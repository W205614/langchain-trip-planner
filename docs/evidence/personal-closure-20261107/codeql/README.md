# 最终源码 CodeQL 复核

扫描源码：`56fef02ce1942fa22a439dfa7d40a2457cc45770`。官方 CodeQL 2.27.4 对 Java、Python、JavaScript 和 Actions 分别执行 security-extended，8 个建库／分析阶段均退出0。

原始阻断等级告警共26项：Java21、Python2、JavaScript3、Actions0。每项的查询、位置、严重等级、有序语义数据流及完整 canonical raw result 均与活跃 b07 复核一致；47份支持源码的 LF 哈希逐份核对相同。恢复校验脚本及其测试的两文件差异已完整阅读，Python 全量分析包含该变更。

- `*.sarif`：本检查点新采集的4份原始报告。
- `analysis-summary.json`：工具身份、阶段结果、覆盖范围与证据哈希。
- `source-snapshot.json`：Git archive 来源及路径／SHA256数组。
- `identity-and-fullraw-comparison.json`：26项身份和完整结果比对。
- `source-review.json`：逐项源码／调用边界、结论及适用条件。
- `policy-draft.json`：待总控独立 load／assess 后启用的策略草稿。
- `reviewed-business-diff.patch`：b07 至本检查点的完整两文件业务差异。

本批次未改动活跃策略，GitHub CI 运行结果按最终提交另行核对。
