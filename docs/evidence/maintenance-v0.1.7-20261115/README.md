# v0.1.7 修复与验证记录


本轮修复单日景点数量未按普通表述执行、后端换 IP 后前端未恢复、攻略原句格式差异导致降级三个问题，并更新审查中发现的 OpenTelemetry 安全依赖。

## 当前交付状态

| 项目 | 最终事实与证据 |
| --- | --- |
| 发布源码 | `ea0f37be5e6b384dfa75859eb2d1f8c75a47e138` |
| Release | [v0.1.7](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.7)，状态 `公开正式版本` |
| 同 SHA CI | [运行记录](https://github.com/W205614/langchain-trip-planner/actions/runs/43683770095)，`completed/success，4/4 jobs` |
| 同 SHA 安全工作流 | [运行记录](https://github.com/W205614/langchain-trip-planner/actions/runs/43683770047)，`completed/success，14/14 jobs` |
| 三应用推广/匿名消费 | [运行记录](https://github.com/W205614/langchain-trip-planner/actions/runs/43693635750)，`publisher/anonymous consumer 两项 completed/success` |
| 最终镜像来源和层身份 | `final-verification.json` |
| 本机七服务升级 | `三应用不可变摘要及修复 Alertmanager 已升级并验证` |
| 数据与配置核对/最终状态 | `46表行数/全行摘要及4上传SHA与升级前一致`；`环境、挂载、端口、进程与运行策略保持`；`七服务全部停止` |

源码、候选镜像、封装镜像、Registry 获取及日常环境的验收范围分别记录。仅当有序层和实际配置身份一致时复用其对应的运行证据。

## 三项问题的原因与修复结果

| 问题 | 原因 | 修复与验证 |
| --- | --- | --- |
| “只安排一个适合散步的公园景点”得到两个景点 | 有限数量解析只识别带每日限定的固定表述；单日普通表述未进入数量约束 | 在单日上下文识别明确数量及形容词，候选、模型提示和最终数量检查一致；旧数量策略的草案检查点不再复用。真实同输入生成1个景点，持久化和刷新一致；多日歧义和范围/否定输入保持约束边界 |
| 后端恢复后前端仍502/504 | 静态代理解析保留旧后端 IP；另需保持健康探针的 Host | 通过 Docker DNS 更新地址，后端IP .2→.3后前端无需重启；URI/body/Request-ID、共享链接缓存、SSE和私有路由通过。实际封装配置一次恢复时间8.63秒，仅为单次测量，非SLA |
| 有证据的攻略回答退为摘录 | 模型看到的标题/正文行被压平，而校验使用原始完整句；列表格式也可能产生不一致 | 保留原句行与完整截断边界、逐句引用，统一处理格式性列表标记；负号、小数、否定、完整句及不合格引用仍严格检查。真实样本返回supported_excerpt_answer；检索返回5个来源；回答只使用其中实际选取的引用，并通过支持检查 |

[真实回归](functional-regression.json) · [封装代理与健康探针](packaged-nginx-health.json) · [源码差异复核](source-delta-review.json) · [实拍页面与来源](../../screenshots/release-v0.1.7/README.md)

## 测试范围与证据时点

| 分组 | 已观察结果 | 适用边界 |
| --- | --- | --- |
| 修复后的数量、候选、最终质量、检查点兼容 | 228项隔离测试通过 | 针对相关真实实现和调用方；不等同于重新跑遍全部UI及所有供应商组合 |
| 攻略、引用支持与离线供应商契约 | 23项隔离测试通过 | 包含合法引用及否定、小数、负号和无支持负例；不额外调用模型验证 |
| OpenTelemetry安全修补镜像 | 56项直接回归通过 | 配套安装/依赖检查及实际镜像实现；随后同持久化行程和5来源读取通过、0新增供应商调用 |
| 后端地址更新 | 实际封装配置换IP后8.63秒恢复 | 前端未重启，业务/健康接口Host与代理契约均检查；不是稳定时延或可用性承诺 |
| 隔离数据库及上传还原 | 7表摘要、4上传字节、还原Java就绪及入口恢复通过 | 固定隔离数据子集，非日常46表全克隆、完整七服务或异地灾备验收 |
| 最终镜像获取与运行 | `三应用匿名拉取、配置/有序层一致，同记录实际登录与刷新通过，0新增供应商调用` | 填写实际Registry摘要及匿名消费终态，不能以本地候选启动代替 |

以上分组可能覆盖相同实现，不合并为独立用例总数。恢复演练的原失败与代理修复后的成功结果均保留。[安全修补镜像复验](security-image-replay.json)。

### 全面检查基线

2026-11-15在旧源码`cc3a09e62d5c6eb7dbbbf9c19318fe0fd0a6e796`上的检查用于发现本轮问题：Python714通过、113跳过；Java213通过（含本地SMTP/S3补测）；浏览器首轮78通过/2失败/2跳过，相关3项复验通过、独立公开模式2项通过，82个功能用例均有通过记录。22业务场景、20公开API检查、42角色流程、66核心场景及21最终故障检查通过。

跳过的Python用例包括可选的固定restic工具与显式启用的运行时/回补/ABI检查，跳过不计通过。上述旧时点检查与[最终同源CI摘要](final-ci-summary.json)分开记录；[公开基线摘要](full-test-baseline.json)保留原失败，不用后续通过结果覆盖。最终同源CI为Python812通过/112跳过、Java213通过、浏览器82个独立用例通过；主浏览器组中另有2项公开模式用例跳过并在独立组实际执行，没有flaky或测试重跑报告。

## 真实模型与工具调用

单日数量回归、带来源攻略回答、RAG及高德服务使用实际配置供应商、合成账号和独立数据库。真实联调新增text2/amap34/embedding6/vision0；截图加载另有amap1。相关维护结束时账本累计text70/amap1849/embedding44/vision2，旧计数未重置。浏览器SDK/瓦片请求不等同于MCP计数，不把截图加载写成零外部请求，也不把本样本用量当作固定生成成本。

[真实输入/结果/增量回执](functional-regression.json)记录原输入、实际1景点、刷新保持、攻略`supported_excerpt_answer`及回答引用支持检查。页面包含180分钟时窗、真实地图/照片和未核验事实提示；[截图](../../screenshots/release-v0.1.7/01-fixed-plan.png)未经业务响应注入或图像编辑。原句匹配证明资料文字支持，不证明资料真实性、相关性或旅行日有效性。

安全补丁后的同记录/来源读取与后续封装验证没有再次生成，必须以具体镜像身份及层关系记录其复用范围。最终封装运行回执：`final-verification.json`。

## 安全修复与扫描结论

Python OpenTelemetry七个关联包升级至1.45.2/0.66b2，修复[CVE-2026-126360](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg)。SDK/API和导出器精确依赖保持兼容；由uv0.13.5重新生成锁，非相关版本/平台条件未改变；真实实现回归确认超长traceparent在正则前被拒绝、正常传播保持。

Alertmanager/Caddy使用OpenTelemetry Go官方修复版本处理[CVE-2026-128085](https://github.com/open-telemetry/opentelemetry-go/security/advisories/GHSA-q7gw-24fr-5p69)。[Python依赖证据](otel-dependency-review.json)与[Go依赖证据](go-security-review.json)分开保留原发现、实际升级和回归范围。

已复核Agent扫描原始227项匹配（High/Critical69项）；精确适用性评估后阻断0、风险接受0。分类包括发行版回补、固定安全补丁、实际组件不存在及受源码/ELF绑定的执行路径不适用；不能称为零原始告警或所有库漏洞均已修复。[19条执行路径复核](agent-execution-path-review.json)保留原reason和分类，并核实7个应用变化、原生文件与依赖绑定；[dpkg-source组件适用性](dpkg-source-applicability/review.json)说明为何不能把源包级匹配直接视为镜像包含受影响Perl提取器，也不宣称dpkg运行包已获得上游修复。

最终三应用扫描与支持镜像的结果摘要见`final-verification.json`；原始扫描明细与文件级绑定证据在本机保留，逐项适用性理由由上述复核文件公开。源码CI与公开镜像的适用检查仍以表头的最终实际终态为准。

## 发布对象与数据保护

发布平台为Linux/amd64。三个应用包分别为Agent、Java业务与前端；配套PostgreSQL、监控及告警接收器按单机部署方案使用，不自动视为本轮公开应用包。

| 应用 | 固定版本引用 | 公开Registry digest |
| --- | --- | --- |
| Agent | `ghcr.io/w205614/langchain-trip-planner-agent:v0.1.7` | `sha256:3a7018149304f63f0f167279b2b9cd118a199c451a917dbf7451d9758ed38806` |
| Java业务 | `ghcr.io/w205614/langchain-trip-planner-backend:v0.1.7` | `sha256:4f7329016e6dab491dec26989d07bffa316477fe0062da46f408a0b1186d559c` |
| 前端 | `ghcr.io/w205614/langchain-trip-planner-frontend:v0.1.7` | `sha256:5418be1c9863f2fa421f092cb781f8cb61c1aa1777ec077440659ce64b82264c` |

新源码包完整核验6916成员和131个项目文件与发布源码的LF等价，原始材料未改写。`container-sources-v0.1.7.zip`的完整SHA为`3d4234017391d022ebf31e5de0805d7b14388ef6ac5433e579c59d7a091885bf`；来源、原始许可证、继承层材料和新增依赖源码均随附并保留历史身份。逐项归档核验回执：`final-verification.json`。

升级前备份包含46表dump、私有配置、4个上传和Agent运行时/Chroma。升级前已核验212载荷SHA/字节，dump TOC为46表/46数据项、211静态文件与日常目录相同；这只是备份字节和目录核验，不冒充完整克隆还原。升级后的46表行摘要/4上传：`46表行数/全行摘要及4上传SHA与升级前一致`；实际镜像/配置挂载：`环境、挂载、端口、进程与运行策略保持`；原镜像及备份保留：`原镜像与备份均保留`；备份任务：`本轮未修改既有备份任务策略`；结束容器状态：`七服务全部停止`。

## 未覆盖范围

公网SMTP、外部S3账号、公网TLS、完整七服务克隆恢复与异地灾备未在本轮验证。本地Mailpit/S3Mock的真实协议/SDK测试通过，不替代上述外部渠道验收。没有真实预订、支付、容量或生产可用性测试；单个真实生成样本不代表跨城市通用成功率。营业、预约、票价、首末接驳、景区内部步行和远期天气仍按来源及实际有效期保留“待核验”。

历史v0.1.6、原始失败和正常标签保留。发布制品、源码身份、日常部署与历史测试边界不混写。

日常索引已按当前Chroma 1.5.9及精确上游源码复核：20张非锁表和189个索引文件保持；仅新增1行启动锁，两个零元素索引的未使用长度缓冲变化。原严格比较失败和快照保留，不把启动字节变化视为文档或有效向量丢失。见[索引启动差异核验](chroma-startup-review.json)。

最终公开摘要：[交付核验](final-verification.json) · [最终CI](final-ci-summary.json) · [旧时点全面审计](full-test-baseline.json)。
