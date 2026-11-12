# v0.1.4：安全修复与离线整栈交付

> 本页为2026-11-11历史边界；当前正式版及OpenSSL后续修复见[v0.1.5](../release-v0.1.5-20261112/README.md)。

核对日期：2026-11-11。项目继续按单机个人旅行应用维护；本页分列源码、发布制品、获取、运行和本机升级，不把任一项代替另一项。结构化摘要见 [verification.json](verification.json)。

## 版本、发布与来源

- [正式Release v0.1.4](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.4)，Release ID441276010，2026-11-11 07:14:19 UTC公开。
- 不可变源码Tag解引用 `272c64b016115e357377b107daefd9bd934a361a`；正常v0.1.0至v0.1.3保留，没有为验证轮次新增版本或覆盖公开资产。
- 三个应用仓库为 `ghcr.io/w205614/langchain-trip-planner-{agent,backend,frontend}`，版本 `:v0.1.4`，Linux/amd64，public；配套服务未新增公开GHCR应用包。

| 组件 | 实际Registry digest | 原应用源码 |
| --- | --- | --- |
| `agent` | `sha256:5785bdc6d48633638066799b1653eeb6bb827b70daa206e118c1e21bd945ea34` | `e81765b257a2b88b1d463a02c6263e754d5cce00` |
| `backend` | `sha256:65058254ba29c7f67124725208645269a9d8d4d4168d5ba1ae9247ad5f384693` | `881817b01cf778da6b546197d85af0534922428f` |
| `frontend` | `sha256:16b10ea4b4be3c32e89a8df3c950346b712b868818377500eaaa244c7d91e24d` | `843d242ff3ac5e4fe18ef6dbf3b2c6d3b51a59ce` |

三个镜像的config摘要、有序解压层、封装与原应用来源分列见[分发手册](../../operations/container-distribution.md)及Release的`container-manifest.json`。Agent候选复用已验收原生层，只覆盖五个Python模块；Java重新构建应用且120依赖JAR逐字节保持；前端复用已验收业务层。许可封装仅附加一层及元数据，不冒充全部原生冷编译。

四附件为镜像归档、对应源码ZIP、manifest和SHA256SUMS。实际完整获取后外层SHA及源码6678成员CRC/内部原始SHA通过；2743来源下载记录匹配。源码111文件中102原始字节同Tag，9为UTF8无NUL文本CRLF/LF差异。校验器只在最后文本源码比较处允许该等价，内外层哈希、二进制与其他字节差异仍严格拒绝。完整通知、补丁、历史层来源和构建材料随源码ZIP提供，不以SBOM代替许可材料。

## 实际CI与发布工作流

| 范围 | 实际结果 | 来源 |
| --- | --- | --- |
| [发布源CI42962698002](https://github.com/W205614/langchain-trip-planner/actions/runs/42962698002) | 4/4 completed/success | 272c64b |
| [发布源Security42962697955](https://github.com/W205614/langchain-trip-planner/actions/runs/42962697955) | 14/14 completed/success | 272c64b |
| [首次推广42978056488](https://github.com/W205614/langchain-trip-planner/actions/runs/42978056488) | publisher成功；旧consumer失败，整个run不是success | 272c64b |
| [独立匿名consumer42979536910](https://github.com/W205614/langchain-trip-planner/actions/runs/42979536910) | completed/success；四附件和三个镜像匿名获取及断网探测通过 | 工具ab15，应用Tag272 |

第一次consumer在`backend/Dockerfile`的CRLF/LF原始字节比较处失败，未改成成功。维护工具修复后新增[只读验收workflow](../../../.github/workflows/consume-published-containers.yml)，使用精确工具提交并单独解引用目标Tag，不重跑publisher、不推送现有版本、不部署生产。独立成功不覆盖原失败。

维护工具提交 `ab15bb4dc8df3300453113fe94e381784ba865e5` 及后续文档与已发布镜像source272不同。其[CI42979412635](https://github.com/W205614/langchain-trip-planner/actions/runs/42979412635)和[Security42979412699](https://github.com/W205614/langchain-trip-planner/actions/runs/42979412699)已实际读到CI4/4成功、Security13/14成功且Agent镜像扫描失败：5个新未评估OpenSSL Unknown ID在3个deb组件上形成15阻断，当时阻断已保留；后续五项上游修复及15条组件匹配的处置在v0.1.5完成，未dismiss或风险接受，不把旧失败改为成功。本地10项相关测试及实际6678成员/111源码比较已通过。

## 修复和安全边界

本轮修复实际CSRF边界和反馈版本溢出：达到整数上限时在事务前拒绝，保留正常递增与陈旧版本冲突行为。预算耗尽现保留明确429错误类型，通过MCP和采集边界传播，避免再误报缺少坐标；普通详情失败仍保留其他合法候选。依赖和原生运行时落实相应升级、回补与验证，前端按需加载。

发布源272的适用未解决告警为0；这是带来源和范围的结论，不等于扫描原始无匹配：

| 对象 | 原始结果与处置 |
| --- | --- |
| Agent最终镜像 | 207 raw：64固定安全升级、118缺席组件、15执行条件、6构建条件、4独立Chroma上下文；未解决/未修复风险接受0 |
| Java／前端最终镜像 | 原始发现0 |
| 独立receiver | 3项有实际回补；适用未解决0 |
| Java CodeQL | 11条GET／跨回调语义误报经逐项源码和完整路径复核、按明确授权关闭；真实算术告警以源码修复处理，不记成误报关闭 |
| 发布源远端 | 当时main/repository CodeScanning open0、Secret open0；Dependabot明确disabled，403不作clean，另有实际CI依赖检查 |

[源码与上下文材料](../native-security-20261110/)及[安全复核政策](../../operations/security-review-policy.md)保留原始报告、修复、判定条件与失败。五个本地支持运行时也完成同库全级门禁。这里不是独立人工审计或完整法律保证；新工具提交的最新远端状态仍须实际查询。

## 离线验收与保留的真实失败

供应商额度耗尽后，用户明确将本轮改为离线验收，不再真实调用模型、高德、Embedding或视觉，不重置调用账本。本地预算/MCP回归63项、维护工具148项通过；后续consumer工具相关10项通过。

- 隔离四服务：42项HTTP合同、7项单日单候选／14:30–17:30排程、生成持久化和刷新检查，以及无候选拒绝负例通过。实际Agent业务管线参与，供应商响应为明确夹具。
- 原两份真实样本仍failed：一份两小时时窗无法容纳活动和接驳；追加调整场景遇模型HTTP429（用户确认额度耗尽）及地图8次预算触顶。不能把这些失败改写为真实供应商通过。
- 发布后：实际本机匿名拉取同一三个不可变摘要，以最终PostgreSQL恢复同一合成库，7用户/3行程/0active，读取并刷新同一保存记录通过。保存内容与版本不变；绑定因时间自然过期，由Java实际只读投影变为待核验，未改时钟或延长事实有效期。
- 真实浏览器登录、行程详情及刷新通过，3张PNG保留原字节；地图SDK和照片代理发送前阻断，未伪造页面、响应或DOM。见[素材和采集边界](../../screenshots/release-v0.1.4/README.md)。四测试服务已停止，合成库归档及列表保留。

累计全局账本text63/amap1656/embedding23/vision1和完整SQL逻辑摘要保持；该计数不等于供应商账单。离线成功不代表真实模型／高德正向验收、真实营业/预约/天气/路线事实通过。

## 已有本机七服务验收

实际沿用原七服务编排、配置和数据，升级三应用至上述公开摘要，并采用四个已验收的本地支持镜像。端口、挂载、安全选项和显式环境配置保持；镜像自身Python/Gosu默认版本变化另行核对。五个Docker healthcheck与Prometheus/Alertmanager/receiver就绪、trip和trip-agent两监控目标up通过。

验收后全部七服务恢复停止。46张业务表全部行摘要、4个上传文件摘要不变，45账号/61行程/3资料/FlywayV20保持。日常只读白名单HTTP，不打开日常浏览器，不发账号/行程写请求；实际outbound账本也包含在全表摘要中。日常不写全局SQLite，故不单凭全局计数不变推断零调用。

原配置、旧镜像、已有两套恢复材料和本轮核心/监控inbox恢复材料保留；自动备份任务仍Disabled。没有删除业务卷、索引或账本，没有做完整七服务克隆恢复。切旧镜像不自动等于数据回滚。

## 文档交付与远端收尾状态

本页历史材料随本轮README文档提交交付main。文档来源不同于v0.1.4镜像来源；文档CI以Actions实际结果及本轮最终回执为准，不为文档重发历史镜像。

已合并且无人占用的临时分支引用已在本地/远端删除，main、现有worktree和正常Tag保留。最近一次owner Maven UI全范围盘点未发现包，本仓关联三个container包，不从API403推断不存在；文档远端生效后的最终远端复核仍待完成。没有清退正常历史版本，也没有为工具修复重发v0.1.4。

上游和文档远端收尾完成后才整理本地普通副本；实际备份、唯一工作、回退资料、配置、业务数据、必要源码/许可、文档素材与最终证据继续保留。本页不充当宿主文件回收回执，不包含私有数据库、凭据或本机绝对路径。

真实RAG/视觉、浏览器地图、SMTP/S3/公网TLS、异地恢复、多实例与生产容量没有本轮新增验收。各历史版本保留各自证据，不视为本版重新全量实测。
