# 2026-09-11 发布验收与项目审查

本次基于 `0c72147` 审查当前工作区，包含行程展示、必去名称、预算、图片和导出修复，以及 README 更新。以下是本地验收与源码审查结论；对应提交的远程结果以 [GitHub Actions](https://github.com/W205614/langchain-trip-planner/actions) 为准。

## 本轮重新运行

| 检查 | 结果 | 证据范围 |
|---|---|---|
| Windows 后端 pytest | 169 passed，27.74 秒 | 独立 SQLite、临时上传/索引目录、上游替身；有一条测试客户端弃用提示 |
| Docker/PostgreSQL 后端 pytest | 169 passed，16.46 秒 | 单独创建 trip-validation，独立 trip_tests 库；迁移至 a2b3c4d5e6f7 |
| 冻结规则评测 | 12/12 通过，策略 constraints-v2 | 合成 POI/路线；旧实现对同组要求 2/12，不是模型准确率对比 |
| 前端生产构建 | vue-tsc 与 Vite 通过 | 仍有大包体提示：入口约 1.52 MB、结果页约 631 KB，均为未压缩构建文件大小 |
| Playwright | 7 passed，约 1.2 分钟 | 3 项显示、1 项导出、3 项任务与隔离测试；完整套件使用隔离服务 18080 |
| 实际行程导出 | PNG、PDF、HTML 下载成功 | 本次会话已检查北京四日记录：PNG 1545×15530，PDF 7 页可解析并渲染；离线 HTML 断网后可展开/收起第 4 天 |
| 发布内容检查 | 未发现当前本机 API 密钥/JWT 密钥进入待提交文件；无运行库、备份和 .env 被跟踪 | 匹配检查不等同于全面安全审计；本机截图、下载与运行日志留在忽略目录 |

复现命令：

```powershell
# backend 目录
python -m pytest -q -p no:cacheprovider --basetemp=.codex-release-pytest --tb=short --show-capture=no
python -m app.evals.constraint_benchmark --output ../docs/evidence/constraint-benchmark.json

# frontend 目录
npm run build

# 仓库根目录；先构建当前镜像；以下数据库必须属于新建隔离栈
docker compose -p trip-validation -f docker-compose.validation.yml up -d --wait postgres backend frontend
docker compose -p trip-validation -f docker-compose.validation.yml exec -T postgres createdb -U trip trip_tests
docker compose -p trip-validation -f docker-compose.validation.yml run --rm tests
$env:E2E_BASE_URL='http://127.0.0.1:18080'
node frontend/node_modules/@playwright/test/cli.js test --config frontend/playwright.config.ts
```

本轮没有重新执行付费模型批量评测、并发压测、完整备份恢复或告警演练。先前运行的结果见 [约束迭代验收](planning-iteration-verification.md)，不能把其日期或样本范围改写成本轮结果。

## 企业面试官视角：可用于什么岗位

以下是基于源码和上述证据的评审意见，不是企业录用结论。以校招或初级 AI 应用后端岗位为目标，项目已经足够作为主项目展开：有从输入、任务、生成、校验、持久化、编辑到导出的完整链路，也有失败处理、权限边界和可复现测试。以中高级后端、高并发基础设施或算法研发岗位为目标，当前证据不足以单独支撑岗位能力。

| 审查维度 | 当前评价 | 面试中需要解释的内容 |
|---|---|---|
| 业务闭环 | 已形成可演示闭环，用户反馈能够转为回归测试 | 必去名称与真实 POI 如何对应；为什么预算未知不能等于免费；导出格式与交互能力的区别 |
| AI 与业务边界 | 有工程设计价值 | LLM 编排、可信候选校验、事实回填和确定性规则各负责什么；真实 POI 不等于行程当天可执行 |
| 异步可靠性 | 适合深入追问 | 持久化任务与 SSE 的关系；断线、取消、进程重启分别发生什么；为什么取消不保证上游停止计费 |
| 一致性与并发 | 有明确可验证机制 | 成功任务/历史/outbox 同事务、修改版本 CAS、索引最终一致性与失败重放 |
| 验证能力 | 比仅展示成功截图更有说服力 | SQLite 与 PostgreSQL 测试差异、上游替身边界、规则通过率与真实生成质量的差异 |
| 维护性 | 存在技术债 | 结果页与规划编排文件较大；名称别名表和费用预留规则需要维护；前端包体仍偏大 |
| 生产运营 | 尚未充分证明 | 缺少真实并发容量、端到端 P95、持续费用记录、真实用户反馈及生产事件证据 |

## 最需要诚实说明的缺口

1. **旅行事实仍不完整。** 开放时间是高德查询参考；预约余票、出行日闭馆、父景点与内部景点重复安排、酒店/餐厅接驳和景区内部步行没有完整硬校验。已有北京样本同时出现故宫与内部交泰殿，不能把规则通过称为人工审核后的最优行程。
2. **名称解析是受控启发式。** 已覆盖城市前缀、城市限定别名和唯一名称变体；拒绝同级歧义，但不是全国景点知识图谱或通用语义实体链接。
3. **预算是估算与预留。** 默认人数、房间数、每晚和交通预留都须按页面假设理解；没有实时酒店报价、门票库存或支付结算。
4. **单进程边界明确。** 当前任务调度、并发门控和部分缓存依赖单 API 进程；持久化不等于多副本任务租约，也不等于集群高可用。
5. **效果和成本证据不足。** 冻结规则案例证明确定性行为；仍需独立真实需求集、人工评分、固定模型/提示词版本及调用费用记录，才能讨论端到端效果。
6. **文档与实现命名需辨别。** 类名中的 MultiAgent 不代表分布式多智能体；当前是 LangGraph 工作流和逐日并发模型调用。RAG 检索指标不等于最终答案准确率。

## 面试准备优先级

优先把已有机制讲透，再增加功能。建议准备三条可以现场打开代码与测试的讲述线：

- **一次任务如何不重复保存：** 从按用户隔离的幂等键讲到状态迁移、取消/超时条件更新和最终事务；证明“数据库保存一次”与“模型只调用一次”不是同一个承诺。入口为 [trip_tasks.py](../../backend/app/services/trip_tasks.py)。
- **一次编辑如何避免覆盖别人：** 从 If-Match 版本讲到执行前检查、最终 CAS、失败提示和前端保留未保存改动。入口为 [history_service.py](../../backend/app/services/history_service.py) 与 [浏览器回归](../../frontend/e2e/reliability.spec.ts)。
- **一次看似合理的 AI 结果如何被纠正：** 用迪士尼别名、故宫/天安门输入、缺价预算和未知路线举例，解释可信事实与规则修复；同时主动指出父子景区及闭馆事实仍是缺口。入口为 [名称解析](../../backend/app/services/attraction_names.py)、[约束校验](../../backend/app/services/planning_constraints.py)。

如果以上三条都能解释取舍、失败路径并现场修改测试，项目可以支撑初级岗位的深入技术面试。若只能复述 README 或演示按钮，即使再增加功能也无法证明独立开发能力。个人贡献和掌握程度不能从仓库自动推断，面试时应如实说明 AI 辅助与本人决策、验证的边界。
