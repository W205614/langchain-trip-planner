# 行程修复源码安全复核

源码：`909fbc455e3dc79e859bdc637e8750702514e820`。官方 CodeQL 2.27.4、`security-extended`，新分析来自 [GitHub Actions 42367525744](https://github.com/W205614/langchain-trip-planner/actions/runs/42367525744)。

四个分析与 SARIF 上传步骤均成功。该分析批次的旧上下文门禁因业务源码树变化拒绝通过；它不是最终整体安全工作流成功记录。

Java 21、Python 2、JavaScript 3、Actions 0，共 26 项原始高危告警。全部完整原始结果、实际解析后的完整规则描述及主告警源码与 `9165c2e` 的原始扫描完全相同，未忽略或规范化差异。47 份支持源码中 46 份未变；Agent `main.py` 仅新增范围保护后的数量约束及修复记录合并，认证、能力路由、出站账本及 Chroma 嵌入式隔离未改变。

新代码的完整差异保存在 [business-source.diff](business-source.diff)。[source-review.json](source-review.json)逐项绑定查询、位置、严重等级、原始结果、语义流及当前源码；[source-snapshot.json](source-snapshot.json)记录精确 Git 树和支持源码指纹。四语言草案门禁本地加载后均无未评估阻断项，再激活根目录上下文配置。每次正式 CI 继续使用新扫描；新告警、源码或数据流变化仍须重新评估。
