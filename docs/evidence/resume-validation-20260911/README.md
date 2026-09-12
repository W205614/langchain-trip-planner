# 简历证据复测：2026-09-11

代码基线：`48fef5b79282b512033c3a51ef18f2240032e043`。本轮新增评测运行器与报告，没有修改业务实现。测试使用独立临时数据库、索引和 `trip-resume-validation` Compose 项目。

## 本轮实际执行

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| Windows SQLite pytest | 169 passed，33.40 秒 | 外部 API 替身，隔离数据库；1 条测试客户端弃用警告 |
| Docker PostgreSQL pytest | 169 passed，18.80 秒 | 新建 trip_tests 数据库，Alembic 迁移后执行 |
| 前端构建 | vue-tsc / Vite 成功 | 存在大包体提示；没有测量线上首屏性能 |
| Docker 镜像构建 | backend / frontend 成功 | 从当前源码构建，未重启日常服务 |
| Playwright | 7 passed，约 1.2 分钟 | 行程展示、下载、离线 HTML、任务恢复、退出、账号隔离 |
| 冻结规则集 | 12/12 | 合成 POI/路线，确定性要求检查 |
| 离线 HTTP 场景 | 20/20 符合预期 | 16 次成功生成全部持久化；15/16 通过规则；含预设失败与降级场景 |
| 容器重启 | 通过 | 断线不取消；重启后 PROCESS_INTERRUPTED；同键不重新生成；没有保存半成品 |
| 备份恢复 | 通过 | 7 张表摘要一致；损坏备份拒绝；确定性嵌入重建索引 |

终端退出码均为 0。SQLite 与 PostgreSQL 运行的是同一套 169 项测试，不能相加成 338 个独立案例。Compose 隔离容器、网络和卷已清理。

## 真实向量检索

使用当前配置的 `text-embedding-3-large`，全新索引，真实嵌入 API，4 城市公共静态攻略、40 条既有标注问题、城市过滤、Top-K=5。没有使用 fixture 排名。

| 指标 | 本轮值 |
| --- | --- |
| Recall@5 | 100% |
| Recall@3 | 100% |
| Precision@1 / 首位命中率 | 87.5%（35/40） |
| Precision@5 | 20% |
| MRR@5 | 0.9333 |
| nDCG@5 | 0.9506 |

每题仅一个标注目标，因此即使目标全部出现在前五，Precision@5 仍只有 1/5。这是相对现有标注的精确率，不能据此断言其余片段全部无用。首位命中率也不是最终答案准确率。

首位未命中：beijing-season-autumn、guangzhou-airport、guangzhou-airport-line、shenzhen-dameisha-booking、shenzhen-xichong-return。

原始证据：[rag-live.json](rag-live.json)、[从同一排名计算 Top-1](rag-top1.json)。`rag-live.json` 的 fact_coverage 对应 Top-3；`rag-top1.json` 的 fact_coverage 对应 Top-1，不得混用。

本轮标注文件 SHA-256 为 `dd994e273c591f2a3bb397cbce72f73b47fe96cde185c5221e7cc161128b2df7`，与旧报告记录不同，不宣称相对旧报告提升或下降。该标注集已经用于开发回归，并非独立盲测集；语料中的票价、开放时间等没有在本轮逐条向官方核实。

## 真实生成样本

预先固定六个场景：北京 1 日、北京 3 日、上海 2 日、广州 2 日、深圳 1 日、杭州 2 日。最终状态见 [live-run-status.json](live-run-status.json)，各场景 JSON 保存逐次结果与实际 Token 用量。

修正环境后本轮结果：6/6 完成生成，覆盖 5 城市共 11 个行程日；6/6 通过 Agent 层确定性质量检查，0 个行程日触发生成兜底。供应商用量合计输入 13,947 Token、输出 8,632 Token，不包含嵌入用量。未配置可验证价格，因此不报告金额成本。汇总见 [planning-summary.json](planning-summary.json)。

范围为直接调用 Agent，包含高德查询、RAG 与真实模型生成；不包含 HTTP/SSE、鉴权、历史保存和路线 API 二次校验。原评测器 `trusted_poi_rate` 仅检查 POI ID 非空，不足以作为事实准确率，因此不用于简历。确定性规则通过不等于行程在出行当天实际可执行。

本轮未建立人工审核的最终答案金标准，故不报告“行程准确率”。也未复做输入压缩前后消融，故简历不再沿用旧版 Token 下降 18.9% 的数字。

## 首次环境失败的处理

`initial-invalid-run/` 保留首次失败的环境记录：隔离 SQLite 未初始化，重建索引读取 knowledge_documents 表失败，原评测器未检查建库结果而输出全零。这不是可用的检索质量结论。补充 init_db 和全空排名检查后，从全新隔离环境完整重跑；不修改标注或检索配置追求更高分。首次中断期间生成的样本不纳入最终统计，最终六个场景全部由修正后的运行器重跑。

## 复现

仓库根目录构建镜像后：

```powershell
docker compose build backend frontend
docker compose -p trip-resume-validation -f docker-compose.validation.yml up -d --wait postgres backend frontend
docker compose -p trip-resume-validation -f docker-compose.validation.yml exec -T postgres createdb -U trip trip_tests
docker compose -p trip-resume-validation -f docker-compose.validation.yml run --rm tests
$env:E2E_BASE_URL='http://127.0.0.1:18080'
node frontend/node_modules/@playwright/test/cli.js test --config frontend/playwright.config.ts
python backend/scripts/resume_recovery_validation.py
docker compose -p trip-resume-validation -f docker-compose.validation.yml down -v
```

backend 目录：

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=.codex-resume-20260911 --tb=short --show-capture=no
python -m app.evals.constraint_benchmark --output ../docs/evidence/resume-validation-20260911/constraints.json
python scripts/business_benchmark.py --output ../docs/evidence/resume-validation-20260911/business-http.json
python scripts/resume_live_validation.py
```

business_benchmark 必须在隔离栈运行期间执行。真实评测读取本机现有 API 配置，会产生实际调用；原始服务日志保留在临时目录，不进入可发布报告。
