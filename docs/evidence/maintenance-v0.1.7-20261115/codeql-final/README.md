# e5db8d4 最终业务源码 CodeQL 复核

来源为 `e5db8d4e6dfdb726429a6b74856193c67a467bfa`，工作流 [43680770075](https://github.com/W205614/langchain-trip-planner/actions/runs/43680770075)。四份原始 SARIF 通过 GitHub artifact API 下载，解压前逐份校验 ZIP SHA-256 与 GitHub digest 完全相同；artifact ID、原始 SHA 和实际 job/step 状态见 `artifact-provenance.json`。

原始扫描结果：Java 高危 11 条、完整 codeFlows 44 条；Python、JavaScript、Actions 各 0。相比前次 bb9fa61，11 条 Java 原始 result 的唯一 JSON 差异都是 `/rule/index: 83 → 102`，完整解析规则、44 条原始 codeFlows 和所有语义 finding 身份不变。逐条比较及新旧 raw 指纹见 `raw-result-differences.json`，不是笼统刷新哈希或宣称 raw-result 字节相同。

独立复核本次源码差异：Python 七个 OpenTelemetry 家族依赖对齐到修补版本，新增 oversized traceparent 在正则前拒绝及正常上下文往返测试；Alertmanager/Caddy 的 Go 模块锁与构建元数据断言更新；健康代理保留调用方 Host，并在真实容器烟测中断言前后端 Host。业务 Java 树、CSRF/认证代码及查询均未变化；原 11 条逐流的具体回调接收者、状态路径提前返回和真实地图计费记录结论经复核仍适用。Go 配方检查及本 CodeQL 报告不代替 Go 依赖扫描或镜像漏洞检查。

在当前业务工作区干净且 HEAD 为上述来源的根目录，实际 `codeql_context.load` 与四语言本地门禁通过：Java 保留 raw 11、逐条 `not_affected_context` 11、阻断 0，其他三语言阻断 0。`final-load.json` 保存实测结果；工具测试为 12 tests 和 26 subtests 全部通过，日志保留于本轮本地 `.cache/release-v017/codeql-final-tests.log`。

原工作流各语言的官方分析及 SARIF 发布步骤成功，但后续原门禁因旧源码绑定失败；这些失败保留，不能把本次本地复核写成原 CI 成功。新增 policy/evidence 元数据提交仍需取得自己的实际 CI 终态。没有关闭查询、调整严重等级、接受风险或新增范围级豁免。前一批 `codeql/` 和更早证据均原样保留。

后续 `62daab9` 元数据 CI 暴露了独立的跨平台行尾缺陷：最后补充 artifact 来源记录时，Windows `write_text` 产生 CRLF，policy 存储了 CRLF 工作区的 review SHA，而 Git 提交的文本 blob 被规范为 LF。嵌套 artifact-provenance SHA 存在同样问题。本次改为 UTF-8 LF 的 `write_bytes`，只修正这两个引用哈希，并为本批字节绑定证据添加显式 `-text`；原始 SARIF、旧 review 和原始 flow 指纹未改。通过临时独立 Git index 构造真实待提交 tree 后执行 `git archive`，对归档解出的规范字节实际运行未经修改的 load 和四语言 gate，全部通过；8 份直接引用的文件 SHA 全部匹配。详见 `line-ending-validation.json`。这比仅在 Windows 工作区加载通过多验证了真实提交 blob 的字节边界，仍不冒充后续远端 CI 终态。
