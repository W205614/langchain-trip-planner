# 高德 MCP 接入

## 改动与适用边界

按照用户后续要求，在保留 LangChain/LangGraph 的基础上，把高德数据访问改为官方 Streamable HTTP MCP。参考[高德官方接入说明](https://lbs.amap.com/api/mcp-server/gettingstarted)与[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。使用已验证的 SDK `mcp==1.30.0`，依赖锁同时补齐其新增运行依赖。

```text
FastAPI / LangGraph 数据节点
        ↓  原有 get_amap_service() 接口
AmapMCPService（缓存、字段转换、POI 详情补全）
        ↓
AmapMCPClient（发现工具、Schema 校验、共享会话、超时）
        ↓  MCP Streamable HTTP
高德官方 MCP Server
        ↓
高德底层地图服务
```

MCP 用于统一发现和调用地图工具。当前工作流仍确定性地执行景点、天气、酒店节点，再让 LLM 基于候选 POI 编排行程。模型使用程序提供的候选，工具调用由程序控制。将来需要自主选择、反复搜索、动态补查时，应另行实现有工具白名单、调用次数与时间预算的模型工具循环。

## 配置和启动

在 `backend/.env` 设置（默认已是 MCP）：

```dotenv
AMAP_TRANSPORT=mcp
AMAP_API_KEY=你的高德Key
AMAP_MCP_URL=https://mcp.amap.com/mcp
AMAP_MCP_TIMEOUT_SECONDS=20
AMAP_MCP_SEARCH_LIMIT=10
```

Key 单独配置，不需要写进 URL；客户端会在连接时添加。URL 要求 HTTPS，本机协议测试允许 localhost HTTP。不要将带 Key 的连接 URL 写进文档或截图。常规应用日志使用已有脱敏格式器；公共异常不附带上游原始文本。

公开入口由 Java 鉴权：Python Agent 的 MCP 提供工作流地图事实与公开 POI 搜索／详情；Java `AmapGateway` 仍通过高德 REST 提供公开天气／路线、手工行程组装及事实／风险核验。两者都使用高德。生成配置时，`AMAP_MCP_API_KEY` 写入 `agent.env` 的 `AMAP_API_KEY`，`AMAP_REST_API_KEY` 写入 `business.env`；前端底图另用高德 JS Key。首次部署按根 README 生成私有配置；已有部署修改实际运行目录的对应配置，再用原部署的 Compose 参数重建相关服务，公网参数见[部署手册](operations/public-deployment.md)。不需要重建业务数据库。

离线回归从项目根目录运行：

```powershell
docker compose -p trip-validation -f docker-compose.validation.yml run --rm tests
```

`backend/scripts/check_amap_mcp.py` 是另行授权后使用的真实探针，不属于上述离线回归。先安装完整锁定依赖，在 `backend/.env` 配好 Key，并准备具有私有权限的 `E:\trip-private\acceptance` 目录；从仓库根目录执行：

```powershell
python -m pip install -r backend/requirements.txt
Push-Location backend
try {
  $env:ACCEPTANCE_BUDGET_FILE = 'E:\trip-private\acceptance\call-budget.sqlite3'
  python scripts/check_amap_mcp.py --output E:\trip-private\acceptance\amap-mcp.json
  if ($LASTEXITCODE -ne 0) { throw '高德 MCP 真实探针未通过，请检查私有报告' }
} finally {
  Pop-Location
}
```

该命令会消耗高德工具调用预算，不调用 LLM；每次发送前预留持久账本中的 `amap` 次数，上限120，失败不退还，重复运行不能更换账本重置额度。运行前核对已授权的剩余额度。账本记录 MCP 工具调用数，工具内部 REST 请求数另计。独立探针没有 Java 任务／operation 上下文，不能直接在要求中央账本的环境运行；固定 `FINAL_*` 验收批次会拒绝 MCP，应使用其 REST 验收流程，不得为运行探针关闭生产账本。成功须退出码0且报告 `passed=true`，并核对 POI 详情 ID、天气、地理编码及三种路线结果。

显式切换 REST：设置 `AMAP_TRANSPORT=rest` 并重建 Agent 容器。MCP 请求失败时按原有工作流返回降级和质量提示。健康接口的 `transport` 与 `connectivity_checked=false` 展示配置状态，实际连通性由上面的真实探针检查。

## 为什么仍有适配代码

| 能力 | 官方 MCP 工具 | 与当前模型的兼容处理 |
|---|---|---|
| 景点与酒店搜索 | `maps_text_search` | 实测搜索列表没有坐标；按 ID 查询详情，每次最多补全配置数量的候选 |
| POI 详情及图片地址 | `maps_search_detail` | `photo` 转为图片列表，顶层开放时间转为已有字段；详情 ID 必须匹配 |
| 天气 | `maps_weather` | 直接使用城市名；兼容扁平 forecast 和嵌套 casts，不先调用地理编码 |
| 地理编码 | `maps_geo` | `results` 转为现有 geocodes 列表 |
| 步行/驾车/公交 | 三个 `maps_direction_*` 工具 | 统一距离、秒数和步行字段，公交传递起终点城市 |

官方公交结果可能不包含每个方案的总距离。仅在方案所有步行、公交分段距离完整且公交备选距离一致时求和；缺项或涉及未覆盖的交通类型则保持未知。不能拿响应根级别的 `distance` 当成选中方案总距离。无法得到有效公交方案时，沿用原服务“查询并核验短距离步行”的逻辑。

MCP 搜索补详情会增加请求数和延迟。保留 POI/天气缓存，新增详情缓存，返回深拷贝防止下游污染；同一工具调用按间隔调度。缓存按进程保存，调用额度由独立账本控制。工具 Schema 来自发现结果，响应还需经过业务事实校验；缺少有效坐标的候选会跳过。

SDK 处理 MCP 握手、消息 ID、HTTP/SSE 消息和工具调用；专用事件循环线程持有共享会话，让现有同步 API/工作流线程可并发使用。调用遵守任务剩余时间并取消超时等待。关闭应用时释放客户端。没有启动本地高德 MCP 子进程。

底图显示依然使用前端高德 JS SDK；图片二进制依然通过原有同源图片代理下载。两者与 MCP 查询分别配置。

## 验证与架构归属

- 当前由 Java 提供公开入口，Python Agent 保留 MCP 查询能力；单测入口为 `backend/tests/test_amap_mcp.py`，完整结果见当前架构验收记录。
- MCP 测试覆盖搜索补详情与缓存、缺坐标/错误 ID、天气字段、公交距离保守转换、跨城参数、短步行回退、禁止静默 REST 回退、错误响应及凭据隐藏。
- 使用本地真实 HTTP MCP 测试服务器验证工具发现、Schema 校验、共享会话并发调用、工具错误、任务超时和关闭。
- 2026-09-12 的旧架构调用报告和测试统计保留在 Git 提交 `6c72a24`，用于查阅当时的架构验证结果。
- 该 MCP 接入测试覆盖地图协议与数据转换；当前真实模型抽样和行程质量问题见[公开运营验收记录](evidence/public-operations-20261107/report.md)。历史测试中的 Starlette/httpx 弃用警告保留在对应记录。

源码入口：`backend/app/services/amap_mcp_client.py`、`amap_mcp_service.py`、`amap_service.py`；测试：`backend/tests/test_amap_mcp.py`；真实调用脚本：`backend/scripts/check_amap_mcp.py`。
