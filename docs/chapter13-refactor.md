# 第十三章对照重构

> 本文记录第一阶段结构重构。随后用户要求迁移 MCP，当前默认地图调用方式已更新，见 [MCP 迁移说明](amap-mcp.md)；下文 REST 描述和测试数量属于该阶段记录。

参考：[Hello Agents 第十三章：智能旅行助手](https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter13/第十三章%20智能旅行助手.md)。2026-09-12 阅读并对照当前仓库；用户确认保留 LangChain/LangGraph。

## 功能对照

| 教程需求 | 当前落点 | 本次处理 |
|---|---|---|
| 景点、天气、酒店查询及行程生成 | `backend/app/agents/` | 拆分提示词、状态契约和数据查询节点；保留并行查询及受控 POI 生成 |
| 地图标注和游览顺序 | `frontend/src/composables/useTripMap.ts` | 独立管理 SDK 生命周期，增删排序及取消编辑实时重绘，离开页面销毁地图 |
| 预算明细 | `backend/app/services/plan_quality.py` | 沿用后端计算；新增景点缺价使用费用预留；单日不计住宿晚数 |
| 添加、删除、调整景点 | `frontend/src/components/trip/AttractionPicker.vue`、`views/Result.vue` | 补上按城市搜索真实 POI、添加至指定日期、全行程重复 ID 禁用、搜索失败重试 |
| PNG/PDF 导出 | `frontend/src/views/Result.vue` | 保留全部日期、多页 PDF 及离线 HTML，执行回归 |
| 前后端分层 | `agents/`、`api/`、`services/`、`models/` 与前端 `router/`、`composables/`、`components/` | 路由从启动文件迁出，地图查询有独立类型与服务封装 |

## 模块职责

```text
backend/app/agents/
  state.py                共享 GraphState，定义节点交换的数据
  prompts.py              整体及单日规划提示词
  data_nodes.py           TravelDataNodes：景点、天气、酒店查询
  trip_planner_agent.py    工作流构建、逐日生成、事实回填、降级与改排

frontend/src/
  main.ts                 仅组装 Vue、路由和 UI 插件
  router/index.ts         页面路由、懒加载和登录守卫
  services/map.ts         复用统一 HTTP 客户端搜索 POI
  components/trip/
    AttractionPicker.vue  搜索、空结果、错误重试及重复选择状态
  composables/
    useTripMap.ts         地图初始化、响应式重绘及销毁
  views/Result.vue        行程页面、编辑保存和导出协调
```

`TravelDataNodes` 作为规划器的数据节点基类，使用规划器提供的高德客户端、进度/追踪回调与必去名称解析；节点只返回自己拥有的状态字段。保留原规划器入口和节点方法，因此任务执行器、API、现有测试及外部导入无需迁移。地图服务继续直接调用高德 REST，不引入 HelloAgents 或 MCP 子进程。

## 编辑行为与数据边界

在结果页选择“编辑行程”，展开需要修改的日期，点击“添加景点”，搜索并选择结果。同一 POI ID 已存在于任一天时不可重复添加。选择器只采用搜索返回的 ID、名称、地址和坐标；没有价格时不写成免费。关闭或重新打开搜索框会使旧请求结果失效，避免旧查询覆盖新结果。

增删、排序及字段修改后旧质量检查失效；取消编辑恢复原行程与检查。地图即时反映景点变化，其虚线仅表示游览顺序，不代表道路导航；地图不可用时其余编辑和导出仍可操作。地图信息窗口对文本转义。

预算在编辑期间明确显示为上次保存值。保存到服务器仍使用 `PUT /api/history/{id}` 与 `If-Match`：后端重算预算与约束，历史更新及 RAG outbox 入队保持现有事务路径。手动编辑结果标记为未经过外部复核，不沿用旧路线时长。发生版本冲突或保存失败时保留浏览器中的修改并显示未保存提示。

## 验证方式

本次实际结果：后端既有完整套件 **169 passed**，新增手动添加保存测试 **1 passed**；前端构建通过；浏览器显示 **3 passed**、编辑 **2 passed**、导出 **1 passed**（分次执行）。后端环境报告一个 Starlette/httpx 弃用警告，Vite 报告既有大体积 bundle 警告。

浏览器初轮失败已定位并修复测试本身：地图替身命名遮蔽原生 `Map`、Ant Design 两个汉字按钮的自动空格，以及导出初始断言缺少等待。修改后针对失败用例重跑通过。没有把模拟接口测试写成真实高德或 LLM 验收。

后端测试使用隔离数据库、目录及假外部凭据，不访问日常业务数据：

```powershell
cd backend
python -m pytest tests -q --basetemp=.pytest-refactor-20260912
python -m pytest tests/test_manual_attraction_edit.py -q --basetemp=.pytest-manual-edit-20260912
```

前端构建与浏览器回归：

```powershell
cd frontend
npm run build
# 在单独终端启动隔离的前端测试服务
$env:VITE_AMAP_WEB_JS_KEY='offline-test-key'
npm run dev -- --host 127.0.0.1 --port 18173 --strictPort
# 另一个终端
$env:E2E_BASE_URL='http://127.0.0.1:18173'
npx playwright test e2e/editing.spec.ts e2e/display.spec.ts e2e/export.spec.ts --reporter=line
```

编辑用例使用模拟 POI、地图 SDK 及历史接口；后端新增用例验证真实服务代码的保存、预算、事务入队、用户隔离和版本冲突。它们不证明当前外部高德/LLM 服务可用，也不构成生成质量或导航准确率评测。
