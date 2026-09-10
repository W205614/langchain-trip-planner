# 行程约束、评测与本地可靠性迭代

## 本轮范围

保持单 API 进程、PostgreSQL、Chroma 与 Docker Compose。默认前端支持显式必去/不去景点、每日安排分钟数、景点间步行上限。用户要求以原样结构化数据传给模型；可信 POI 和确定性校验负责最终边界。

`PlanningConstraints` 保存在任务请求和最终 plan JSON 中，历史编辑从服务器原记录还原，不能用客户端传来的空约束覆盖。完整景点名称去空白、忽略大小写匹配，不做不可靠的模糊别名推断。相互冲突的必去/不去请求返回 422。必去名称未查到或未选入时明确报告，不能虚构 POI。

生成候选先按主要经纬度方向排序后连续分组，减少轮流分配导致的跨区混排。这是启发式，既不承诺全局最优，也不能代替真实导航。最终校验跨天 POI 重复、不去景点、每日总时间和景点间步行距离，移除可选景点时记录原因；必去景点超限时保留并报告冲突。手动编辑只报告违规，不静默删除用户内容。改排也只报告，避免无意修改其它日期。

每日总时间包括景点游览、景点间导航、90 分钟用餐预留和30分钟缓冲。它不包含已验证的餐厅/酒店路线或预约时段。公交接口未提供步行分段时，步行上限标为未核实。开放时间、预约、景区内部步行和父子景区关系目前仍没有可靠事实覆盖，不能宣称整份行程已经可执行。

质量报告分别保存 `rules_passed`、`facts_complete`、`feasibility_status`、`day_checks`、`repairs`。规则通过仍显示需要核实。聚合导航数值仅累计可用段，是否完整以 `route_checked` 和逐日的 nullable 字段为准。

## 可重复评测

在 backend 目录执行：

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=<新的临时目录>
python -m app.evals.constraint_benchmark --output ../docs/evidence/constraint-benchmark.json
```

冻结集 `evals/constraint_cases.json` 使用合成 POI 与路线替身，明确预期景点、规则状态和未知数据行为。覆盖重复、跨天重复、不去、总时长、保护必去、不可满足必去、步行上限与上游缺失。报告保存数据集摘要和规则版本，任何新版本不满足预期都退出非零；CI 保存报告。

基线为旧 normalize_day + repair_plan_routes + evaluate_plan，新旧使用相同原始草稿和路线替身。这是新增约束场景的验收比较，不能称为模型准确率提升；特意选取困难场景，亦不代表真实旅行需求分布。后续真实模型评测需单独配置费用上限、固定模型与提示词版本并人工评分，当前未运行。

## 任务生命周期

`GET /api/trip/tasks` 按用户分页；`POST /tasks/{id}/cancel` 幂等取消；`POST /tasks/{id}/retry` 要求新的稳定 Idempotency-Key。失败或取消任务可显式重试，同一个重试键返回同一新任务。原失败任务保留。跨用户查询/取消/重试返回404。

前端新增“我的任务”。单日改排使用 `POST /api/history/{id}/revise-task`，保存原记录ID、原版本、改排指令。工作线程执行前检查版本，最终保存再次 CAS 校验；任务成功状态、原行程更新和 outbox 同事务。旧同步 revise-day 也适配同一执行路径。刷新后可在任务列表取回结果。

取消后不能写入成功结果；已发出的外部调用无法保证停止计费。调用间检查任务状态，调用用量在任务的 usage_json 中保存，包括返回用量的失败/重试路径。上游没有返回 usage 的调用只有调用计数，不伪造金额。

默认每用户同时最多4个排队/执行任务、UTC日界每日50个、全局每日500个。对应 TRIP_USER_ACTIVE_LIMIT、TRIP_USER_DAILY_LIMIT、TRIP_GLOBAL_DAILY_LIMIT。额度以数据库任务记录计数，重启不重置，取消不退任务额度。额度是任务级，不是金额预算；资料解析与嵌入不在该额度内。仍仅支持单 API 进程。

`POST /api/auth/logout` 增加账号 token_version，撤销该账号所有旧凭证。前端服务器退出成功后再清空本地登录态。新登录拿到新版本；不是仅关闭标签页。已建立的订阅不承诺即时踢下线。

## 本地通知与恢复

隔离测试栈新增 Prometheus、Alertmanager、本地通知接收器，只暴露127.0.0.1端口。执行以下命令前准备好隔离后端和前端；它会停止并重新启动 trip-validation 的后端，不操作日常项目：

```powershell
python backend/scripts/notification_smoke.py
python backend/scripts/recovery_drill.py --output docs/evidence/recovery-constraints.json
python backend/scripts/lifecycle_smoke.py
```

队列预警改为超过120秒持续30秒，另监控任务超时计数变化。通知演练验证真实规则触发与恢复送达；仅本地接收器，不代表邮件/飞书送达。生产Compose的外部通知仍需配置真实接收渠道。

`check_backup_retention.py <备份根目录> --keep 7` 校验完整备份的摘要并提出保留建议，不执行删除。摘要正确不等于恢复成功，异机副本和自动备份调度仍由实际运行环境决定。已有 recovery_drill 验证数据库与派生索引恢复，不能用本机目录复制来宣称异地容灾。

本轮新增迁移 a2b3c4d5e6f7；日常部署升级前先备份，再更新镜像并迁移。不要把测试栈替换为日常数据库。回滚必须考虑旧代码与 schema 的兼容关系，本文不承诺零停机或自动回滚。
