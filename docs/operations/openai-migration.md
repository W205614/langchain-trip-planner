# OpenAI 接入与索引迁移

文本规划、改排和资料问答使用 `gpt-6.1-sol`，图像／扫描 PDF 提取使用同一模型；向量化使用 `text-embedding-3-large`。默认端点为 `https://api.openai.com/v1`。高德仍是地图事实来源，Chroma 和本地排序不迁移到托管 File Search。

## 配置与凭据

在私有 `backend/.env` 填写 `LLM_API_KEY`。`LLM_MODEL_ID=gpt-6.1-sol`、`LLM_REASONING_EFFORT=low`；视觉和 Embedding 默认继承该账号，也支持独立设置 `VISION_API_KEY`、`EMBEDDING_API_KEY`。三个 Base URL 均设为官方端点；Embedding 设 `EMBEDDING_MODEL=text-embedding-3-large`、`EMBEDDING_DIMENSIONS=3072`。

不要保留旧第三方 Base URL 并仅替换密钥。密钥只进入后端私有配置，不能放入 `VITE_*`、README 或命令输出。已有安装使用 `prepare_java_deployment.py --upgrade-existing` 同步明确填写的模型配置；该命令保留数据库、JWT 和内部服务密钥，重建容器才会加载新值。

GPT-6 请求使用 reasoning 参数，不发送旧温度参数；输出上限仍有界。模型、reasoning、输出预算和提示版本影响可恢复检查点兼容性。模型访问、余额或连接失败会明确失败或按既有业务规则展示降级，不会悄悄切回旧供应商。

## 接入检查

主机 Python 需先安装仓库锁定依赖。默认只查询模型访问权限，不生成内容：

```powershell
python backend/scripts/openai_check.py --report .codex-public/openai-access.json
```

真实检查会计入调用预算，账本路径必须绝对且持久；重复运行不会重置账本：

```powershell
python backend/scripts/openai_check.py --live --roles text,vision,embedding `
  --budget-file E:\trip-private\acceptance\call-budget.sqlite3 `
  --report E:\trip-private\acceptance\openai-live.json
```

上限是文本 12 次、视觉 1 次、Embedding 40 次、高德 MCP 工具调用 120 次。真实项目验收与此检查应共用账本；MCP 内部请求数不等于这里可观测的工具调用数。报告区分 `accessible` 与 `live_checked`，不能把模型列表可读当作生成成功。

## 已有向量迁移

新索引身份包含供应商、端点、模型和维度。同名模型的第三方向量及缺少身份的旧索引均不能直接视作兼容；系统保留旧数据并提示需要重建。初始化和健康检查不会为了验证维度调用 Embedding。

使用已完成 MFA 和邮箱验证（公开模式）的管理员 API Bearer token 设置 `TRIP_ADMIN_TOKEN`。先预览，再执行：

```powershell
python backend/scripts/rag_migrate.py --base-url http://127.0.0.1:8380
python backend/scripts/rag_migrate.py --base-url http://127.0.0.1:8380 --execute
```

预览读取业务快照，输出文档数量、输入 UTF-8 字节及请求数量估算；字节数不是供应商 Token 或费用。执行每 16 个文档保存一次进度，网络中断后重跑执行命令会续接相同快照。相同批次使用稳定文档 ID，响应丢失时重放不会重复插入。

业务快照、来源内容或索引身份改变时停止续接；重新预览后可用 `--execute --restart` 建立新一代。新集合数量、检索和业务版本均通过后才原子切换，失败保留原集合。完整数据重建需要独立预算，不把小样本通过等同全量迁移。

注销／资料删除会清理活动、保留及未完成的索引集合，并写版本墓碑；这个删除路径不依赖模型或 Embedding 可用。

## 用量解释

发送前的调用与 Token 预留用于额度控制，失败不代表供应商未计费。非流式文本、视觉和 Embedding 的供应商 usage 单独回报；流式规划保留任务 usage。缺失回报保持未知，不填成零；两个口径不能相加重复估价。单价为零表示未配置估价，真实费用以供应商账单为准。

接口和模型依据：[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[迁移说明](https://developers.openai.com/api/docs/guides/latest-model)、[Embedding](https://developers.openai.com/api/docs/guides/embeddings)。
