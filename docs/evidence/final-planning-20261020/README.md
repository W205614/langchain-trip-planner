# 最终版开发与验收记录

日期：2026-10-20。部署形态：单机 Java、Python Agent、PostgreSQL、Vue/Nginx 四服务，Chroma 由 Agent 管理。此目录仅含脱敏案例与汇总，不含账号凭据、供应商密钥、备份和原始私有运行配置。

## 证据与当前门禁

|层次|证据|所证明的范围|
|---|---|---|
|时间规划对照|[time-window-benchmark.json](time-window-benchmark.json)|六个合成开发案例：路线最短但营业窗冲突、路线缺失、事实过期、必去闭馆、可选闭馆、晚到；不代表模型准确率|
|真实 HTTP 角色与事务|[roles-http.json](roles-http.json)|真实隔离账号、Java、PostgreSQL和真实Agent流程；模型／地图为离线替代，42项检查|
|跨城、预约和局部范围|[core-scenarios-http.json](core-scenarios-http.json)、[cross-city-hotel-http.json](cross-city-hotel-http.json)|65项核心检查；最后酒店／早班次变更另有12项受影响检查，不与前者相加当独立案例总数|
|故障与恢复|[faults-http.json](faults-http.json)|取消晚回、旧执行隔离、恢复幂等、管理员边界、事实过期；以 fully_executed 及未执行清单判断完整性|
|恢复与升级|[restore-upgrade.json](restore-upgrade.json)|实际挂载归档校验、23表539行恢复；V10升级后原22业务表原列与9条历史迁移不变|
|浏览器角色|[browser-roles.json](browser-roles.json)、[所有者时间轴](browser-owner-timeline.png)、[撤销成员](browser-revoked-access.png)|真实账号和后端；仅阻断外部高德SDK，验证约束保存、时段锁定、完成和撤销缓存失效|
|真实供应商抽样|[live-plan.json](live-plan.json)、[live-proposal.json](live-proposal.json)、[live-crosscity.json](live-crosscity.json)、[live-research.json](live-research.json)；初始失败独立保留|独立真实输入、真实模型／Embedding／地图；有固定持久预算；不使用夹具结果冒充|
|最终交付|[release.json](release.json)|日常容器镜像、挂载、清理；最终提交CI以GitHub对应SHA运行及交付记录核对|

未生成证据或报告明确未执行的项目不得标为通过。最终四项停止线结论在交付汇总中记录；开发期间的成功不替代最终提交 CI。

## 已发现并修复的问题

|实际问题|根因与修复|验证位置|
|---|---|---|
|单日改排后处理改到其他日期|限制后处理范围，Java比较范围外业务内容|test_actual_finalize_pipeline_cannot_modify_another_day、FinalPlanningIntegrationTest|
|旧行程第一次改排被误判越界|稀疏历史字段被默认值补齐；只归一化声明默认值，输出保留未设置字段|legacyTwoDayProposalAcceptsOnlyDefaultHydrationOnUntouchedDay|
|新增预约仍服从旧时间轴|当前确认预约应成为约束，旧时间仅是改动偏好；锁定／完成时间仍受保护|updatedConfirmedAppointmentConstrainsNewScheduleRatherThanOldTimeline|
|允许入场窗口被当作离场期限|分离入场窗口、活动持续和固定时段语义|admissionWindowAllowsLaterVisitEndButRejectsLateEntryAndSemanticOutsideChange|
|手工编辑后旧时间仍可锁定|仅将受影响日标为过时，并拒绝基于旧时间的新锁定／完成|manualScheduleInvalidationPreservesUntouchedDayAndRejectsStaleTimeActions|
|总体未核实的提案漏绑定已使用营业事实|按实际使用事实绑定依赖，整体质量未知不取消过期校验|unverifiedOverallScheduleStillBindsUsedOpeningFactsAndDuplicateIdsHaveCode|
|单活动保护将下午预约排在早餐之前|受保护景点和餐饮被错误串成一组顺序；只保持受保护景点相对顺序，餐饮按自身时间锁|test_partial_scope_with_meals_does_not_order_afternoon_booking_before_breakfast|
|范围内旧活动的新时间可以越过授权窗口|Python给新／可变活动增加时间域，Java最终时间检查也覆盖保留旧ID的可变活动|剩余时段与旧ID时间越界反向回归|
|带预订账本的规划输入原样保存被拒绝|数据库与HTTP的等价数值节点类型不同；按严格JSON数值语义比较，字段和金额实改仍拒绝|真实GET→PUT成功、金额+1返回422且原金额不变|
|照片请求漏计外部预算|照片发送前预留持久额度，预算依赖强制注入；拒绝时返回占位且不出网|AmapGatewayTest预算拒绝HttpClient零调用|
|换酒店仍从当晚酒店出发，跨城漏原城市接驳|分别使用前晚／当晚端点，原城市路线独立计算；缺端点或路线保留未知，不用同城路线假算跨城|换酒店／原城接驳专项与cross-city-hotel-http.json|
|分城市生成误删非全程末日住宿|分段保留全程仍需要的首晚可信候选，真正末日不加额外住宿|真实离线生成流水线分段住宿回归|
|早班次或接驳早于用户每日最早出发|固定交通和接驳也检查原始每日硬边界；正例要求用户显式允许07:00开始|09:00、08:00边界负例及07:00正例|
|普通任务幂等键能冒充恢复，新任务和祖先关联不原子|只复用已关联同祖先的恢复任务，创建与关联同短事务，网络探活在事务外|恢复键冲突／关联失败整行回滚数据库回归|
|连续两次恢复漏读更早祖先有效阶段|同用户、去环、有界读取祖先快照，按阶段及兼容信息去重；Python再核验哈希和有效期|二次恢复祖先专有阶段与最近阶段回归|
|图片缓存混用跨城同名地点|按日期城市和POI身份取图，保留实体参数|前端跨城同名图片回归|
|导出丢失活动名称|可点击标题在清理控件前转静态文本|PNG/PDF/HTML时间轴导出回归|
|时间轴直接显示内部错误码|翻译原因并关联活动名|final-planning.spec.ts|
|首次离线实际规划出现重复POI|夹具误读酒店／其他日白名单，修正夹具选取区段；生产实体校验继续拒绝重复|test_validation_fixture_pipeline.py|
|真实规划因远端早餐候选整体无解|普通AI餐厅改为可选候选，省略时明确列餐饮缺口；用户用餐窗口、预约、锁定和完成仍硬保护|原真实草稿及77路线离线复现；live-plan-initial-failure.json、live-plan-recovery.json保留先失败后修复证据|
|恢复求解重复查询旧失败路线，路线质量又发重复查询|复用未核实占位并保持未知；质量汇总复用排程路线，未知距离传播为null|完整v2恢复离线拦截所有新付费发送；实际恢复任务完成时预留零增量|
|未知日距离汇总成0|全程聚合只有各日全已知才给总量，不把未知当0|test_route_totals_do_not_hide_a_missing_day_as_zero；旧真实快照保留，未付费重跑|
|跨城推荐住宿未考虑次日固定出发|原真实酒店到站62分钟，07:00最早出发赶不上08:00；仅对可变推荐酒店从有效候选中最多3家核验接驳，不改班次与硬边界|保留live-crosscity-initial-failure.json；原缓存完整v2离线恢复，固定/锁定/范围外住宿反例|
|局部改排顶层餐饮缺口遗漏保留日期|从全部持久日的feasibility汇总缺口，范围外day内容不变|test_scoped_quality_retains_meal_gaps_from_untouched_days；manual-live-review.json保留旧样本发现|

其他已修复测试工具问题：Nginx拒绝内部路径可返回HTML404，HTTP验收现在按状态检查后只解析JSON内容；浏览器截图等待明确拒绝态，避免拍到尚未加载页面。没有通过删除业务断言或接受失败结果让验收通过。

## 复现

在独立 Compose 项目启动 `docker-compose.validation.yml`，严禁替换成日常数据库。脚本先检查离线标识，故障脚本还校验容器项目标签：

```powershell
python backend/scripts/final_acceptance.py --output evidence/roles.json
python backend/scripts/final_scenarios.py --output evidence/scenarios.json
$env:INTERNAL_SERVICE_KEY='与隔离Compose一致的内部密钥'
python backend/scripts/final_fault_acceptance.py --backend-container trip-validation-backend-1 --output evidence/faults.json
cd frontend
$env:E2E_BASE_URL='http://127.0.0.1:18080'
npx playwright test e2e/final-roles-live.spec.ts
```

故障脚本会真正重启指定隔离后端，请顺序执行，避免其他场景同时间被打断。源码测试使用独立 PostgreSQL，测试类与脚本入口见本轮提交。仅重测失败及直接受影响路径，最终完整回归由 GitHub CI 执行。

## 真实调用与结论边界

四个真实样本已分别通过，先前失败原报告保留。最终持久预留为文本6、Embedding20、高德392，详见[live-budget.json](live-budget.json)。首个失败排程复用草稿和77条路线，恢复任务完成时计数零增；跨城修复只补44条新路线边，未重跑成功模型／RAG。落库后索引的嵌入计入总量。通过的真实样本没有再次生成。预留计数不是人民币扣费金额，供应商账单与免费额度需在供应商侧核对。

实际样本的营业、票价、部分餐饮及路线仍有明确未知。跨城接驳没有可靠的进站／安检／检票提前量数据，已追加服务端未知提示和兼容旧行程的前端提醒，并以离线回归验证，没有再次付费抽样。班次与费用为测试所有者输入的假设确认事项，绝非实际出票。

本轮固定编号 `final-20261020-050d19bce4`，文本12、Embedding40、高德工具与REST合计400。发送失败不退回，旧账本保留；额度不足不能换号继续。每个真实报告记录调用前后计数，最终从 Java 持久计数核对。已发送但响应未持久化按不确定处理，不承诺费用 Exactly Once。

历史开发集的40/40、32组与QPS数字没有作为本轮结论继续引用。当前记录区分合成案例、离线实际HTTP、浏览器及真实供应商抽样，不把单机测试写成企业SLA、长期生产证明或30日性能保证。

## 交付核对

日常四服务与原挂载、历史兼容及重建后精确读取均已核对；验收绑定已解除，原账本保留。清理11个本次验证容器、3个隔离网络及9个经归档验证的项目镜像，未删卷、配置、备份或恢复文件。其他项目并发改变状态已在本地前后快照记录，本任务没有对其执行变更。

本轮 runtime 证据源版本为 f64e579，后续文档提交及正常合并不改变功能代码。最终主分支 SHA 的四项预期 CI 必须全部成功，运行镜像标签会与最终 SHA 再次核对；GitHub 的对应提交运行记录是 CI 结果事实源，不以本文件快照替代。
