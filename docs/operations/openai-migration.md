# OpenAI 接入与索引迁移

文本规划、改排和资料问答使用 `gpt-6.1-sol`，图像／扫描 PDF 提取使用同一模型；向量化使用 `text-embedding-3-large`。默认端点为 `https://api.openai.com/v1`。高德仍是地图事实来源，Chroma 和本地排序不迁移到托管 File Search。

## 配置与凭据

在私有 `backend/.env` 填写 `LLM_API_KEY`。`LLM_MODEL_ID=gpt-6.1-sol`、`LLM_REASONING_EFFORT=low`；视觉和 Embedding 默认继承该账号，也支持独立设置 `VISION_API_KEY`、`EMBEDDING_API_KEY`。三个 Base URL 均设为官方端点；Embedding 设 `EMBEDDING_MODEL=text-embedding-3-large`、`EMBEDDING_DIMENSIONS=3072`。

```dotenv
LLM_API_KEY=your-private-openai-project-key
LLM_MODEL_ID=gpt-6.1-sol
LLM_BASE_URL=https://api.openai.com/v1
LLM_REASONING_EFFORT=low
VISION_MODEL_ID=gpt-6.1-sol
VISION_BASE_URL=https://api.openai.com/v1
VISION_REASONING_EFFORT=low
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_DIMENSIONS=3072
```

不要保留旧第三方 Base URL 并仅替换密钥。密钥只进入后端私有配置，不能放入 `VITE_*`、README 或命令输出。已有安装使用 `prepare_java_deployment.py --upgrade-existing` 同步明确填写的模型配置；该命令保留数据库、JWT 和内部服务密钥，重建容器才会加载新值。

GPT-6 请求使用 reasoning 参数，不发送旧温度参数；输出上限仍有界。模型、reasoning、输出预算和提示版本影响可恢复检查点兼容性。模型访问、余额或连接失败会明确失败或按既有业务规则展示降级，不会悄悄切回旧供应商。

## 接入检查

主机 Python 需先安装完整后端锁定依赖。以下命令从仓库根目录执行，默认只查询模型访问权限，不生成内容：

```powershell
python -m pip install -r backend/requirements.txt
python backend/scripts/openai_check.py --report .codex-public/openai-access.json
```

真实检查会计入调用预算，账本路径必须绝对且持久；重复运行不会重置账本：

```powershell
python backend/scripts/openai_check.py --live --roles text,vision,embedding `
  --budget-file E:\trip-private\acceptance\call-budget.sqlite3 `
  --report E:\trip-private\acceptance\openai-live.json
```

上限是文本 12 次、视觉 1 次、Embedding 40 次、高德 MCP 工具调用 120 次。真实项目验收与此检查共用账本；高德按可观测的 MCP 工具调用计数，工具内部 REST 请求数另计。报告的 `accessible` 记录访问权限，`live_checked` 记录实际生成检查。

成功判据为进程退出码0、报告 `passed=true`；访问检查还需三角色 HTTP200，真实检查需对应角色 `live_checked=true`、文本非空、视觉颜色识别通过和 Embedding 返回3072维。401／403、余额、网络及预算错误按报告排查后重试。检查脚本读取 `backend/.env`；部署时还需生成／升级运行配置、重建容器，并检查容器实际调用链路。

## 已有向量迁移

新索引身份包含供应商、端点、模型和维度。第三方向量及缺少身份的旧索引需要重建，系统会保留旧数据并提示迁移。初始化和健康检查使用本地身份信息，避免额外 Embedding 调用。

使用已完成 MFA 和邮箱验证（公开模式）的管理员 API Bearer token 设置 `TRIP_ADMIN_TOKEN`。先预览，再执行：

```powershell
python -m pip install httpx==0.28.1
python backend/scripts/rag_migrate.py --base-url http://127.0.0.1:8380
python backend/scripts/rag_migrate.py --base-url http://127.0.0.1:8380 --execute
```

公开部署将 `--base-url` 换成实际 HTTPS 来源；令牌只从 `TRIP_ADMIN_TOKEN` 环境读取。预览必须返回 `success=true, preview=true` 且计数符合预期；执行需 `success=true, preview=false` 并返回新的 `generation`，再以受权查询核对命中及隔离。

预览读取业务快照，输出文档数量、输入 UTF-8 字节及请求数量估算；字节数不是供应商 Token 或费用。执行每 16 个文档保存一次进度，网络中断后重跑执行命令会续接相同快照。相同批次使用稳定文档 ID，响应丢失时重放不会重复插入。

业务快照、来源内容或索引身份改变时停止续接；重新预览后可用 `--execute --restart` 建立新一代。新集合数量、检索和业务版本均通过后才原子切换，失败保留原集合。其他部署的全量重建先预估输入和调用次数，再安排预算。

注销／资料删除会清理活动、保留及未完成的索引集合，并写版本墓碑；这个删除路径不依赖模型或 Embedding 可用。

## 本机全量迁移结果（2026-11-07）

本机日常三角色现连接 OpenAI 官方端点：文本／视觉 `gpt-6.1-sol`、Embedding `text-embedding-3-large`（3072维），模型访问检查均为 HTTP200。日常全量索引迁移已成功，覆盖46条可索引行程、125块，使用13次 Embedding，活动 generation 为 `42efb7e7a2b449b5b13f44a065bab01a`，状态为 `enabled`。

该全量迁移单独记录在[个人项目收尾验收](../evidence/personal-closure-20261107/report.md)，原[真实模型小样本](../evidence/public-operations-20261107/report.md)保留原结果。持久验收账本累计为文本5/12、视觉1/1、Embedding23/40、高德62/120，沿用原账本；后续真实调用仍受剩余额度限制。

## 用量解释

发送前的调用与 Token 预留用于额度控制，已发出但失败的请求仍可能计费。非流式文本、视觉和 Embedding 的供应商 usage 单独回报；流式规划保留任务 usage。缺失回报保持未知，重复描述同一次调用的用量只计一次。单价为零表示未配置估价，真实费用以供应商账单为准。

接口和模型依据：[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[迁移说明](https://developers.openai.com/api/docs/guides/latest-model)、[Embedding](https://developers.openai.com/api/docs/guides/embeddings)。
