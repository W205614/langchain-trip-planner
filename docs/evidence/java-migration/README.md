# Java 业务后端 + Python Agent 验收记录

日期：2026-09-15。范围：单 Windows 主机 Docker，单 Java、单 Python、独立 PostgreSQL；不代表生产 SLA、长期压力测试或旅行事实准确率。旧 Python 架构证据不与本表累加。

## 整理后的当前验收（2026-09-15）

用户要求只保留当前 Java 业务后端和 Python Agent，并合并日常 Docker 项目。清理分支为 `codex/cleanup-java-agent`；[cleanup.json](cleanup.json) 是本轮脱敏结果。

- Java 39 项、Agent 196 项、HTTP 业务 20/20、Java 账号／偏好／权限接口 20/20、Playwright 12/12 通过。
- 恢复演练七表及上传内容一致；源服务健康和公开入口就绪后才判定通过。资料人工审核发布、Java 重启、Python 断流、取消后迟到均复验通过。
- 浏览器首轮最后 3 项因与停服务恢复演练重叠失败，调整执行顺序后完整重跑 12/12；没有修改断言或降低门槛。
- Agent 从官方 Python 基础镜像重新构建，锁定依赖安装与 `pip check` 通过；新增部署配置和密钥脱敏检查。运行镜像无旧业务 API／ORM 源码、无用户本地 RAG 验证脚本，未接收数据库或 JWT 凭据。
- 日常 Agent 已更新，`langchain-trip-planner` 下仅四服务且全部健康，`http://localhost:8080` 返回 200。
- 删除 15 个本项目验证／恢复／测试库容器；25 个其他项目及独立容器的 ID、状态、启动时间不变。所有原有 Docker 卷、宿主数据、备份及旧镜像保留，不执行全局 prune。
- 删除旧 Python 业务接口、ORM、鉴权、任务调度、Alembic、旧部署／迁移脚本和过时证据文档。保留生成所需纯函数与冻结对照，最终业务裁决仍只由 Java 执行。旧源码可从 `6c72a24` 或 `E:\project\trip-planner-backups\pre-cleanup-source-20260915.zip` 恢复到独立目录。
- 用户未跟踪的 `backend/scripts/rag_current_validation.py`、`docs/evidence/rag-current-20260913/` 原样保留，不提交；本机忽略的缓存和历史数据不是当前运行源码。
- 本轮只执行离线夹具测试，没有追加模型、视觉、embedding 或高德付费调用。清理后的测试数与下方迁移阶段 255 项不能直接比较为覆盖率。

远程 CI 是提交后的独立门槛，以最终提交对应运行结果为准。

## 迁移阶段已执行（清理前历史记录）

| 检查 | 结果 | 原始证据/复现入口 |
|---|---|---|
| Java 单元与真实 PostgreSQL 集成 | 39 通过，包括 12 个原冻结约束场景、幂等竞争、用户额度、版本/所有者、取消/截止时间、事务回滚、发布重试及图片缓存契约 | `business-backend/target/surefire-reports/`（本地生成，CI 上传） |
| Python 全量回归 | SQLite 255、PostgreSQL 255 分别通过，含旧业务参考基线及新 Agent/预算/索引/解析/协议边界、统一图片回退与真实解码、恢复就绪检查 | 隔离镜像 pytest / Compose `run --rm tests` |
| 公开 HTTP 业务场景 | 20/20 通过，原预期和阈值未放宽 | [offline-business.json](offline-business.json) |
| Nginx HTTP 冒烟 | 健康、管理员权限、内部指标隔离、四类上传边界通过 | `backend/scripts/smoke_http.py` |
| Playwright | 12/12 通过；真实 Java 登录/任务恢复与浏览器编辑/导出等范围 | `frontend/e2e/`；模型/地图为隔离替身，部分显示测试拦截响应 |
| 资料复核/发布 | 权限、原文件、解析后等待复核、过期版本拒绝、当前版本发布、删除隐藏 | [knowledge.json](knowledge.json)，解析/向量为明确离线替身 |
| Java 重启/断开前端 | `PROCESS_INTERRUPTED`、同键不重新生成、前端断开不取消后台任务 | [lifecycle.json](lifecycle.json) |
| Python 停机/取消后迟到 | `AGENT_CONNECTION_LOST`、同键不重生成、取消不产生历史 | [cross-service-faults.json](cross-service-faults.json) |
| Prometheus/Alertmanager 通知 | 本地接收器收到 firing/resolved；不是生产通知通道 | [notifications.json](notifications.json) |
| 隔离库恢复 | 七表行摘要一致、上传字节一致、Java 就绪、截断归档拒绝 | [isolated-recovery.json](isolated-recovery.json) |
| 实际原数据导入 | 9 账号、25 行程、2 资料及相关作业逐字段核对 | [daily-import.json](daily-import.json) |
| 旧索引副本兼容 | 82 公开向量、5 历史向量、3072 维；复制后检查，无 embedding 调用 | [chroma-copy.json](chroma-copy.json) |
| 日常新架构备份恢复 | 切换后七表摘要一致、恢复 Java 就绪、未改日常配置 | [daily-restore.json](daily-restore.json) |

该阶段 Java 测试使用独立 PostgreSQL 17.6；混合 Python 回归曾使用隔离 `trip_tests`。清理后 Agent 测试不再需要业务数据库。运行时入口为 `app.agent_api.main:app`，导入边界测试确保不导入业务 ORM。公开入口保持 Java/Nginx；Python 不暴露宿主端口。

## 真实小样本

[live.json](live.json) 保留任务 ID、用量、分类及资料状态，非匿名生产用户数据集：

- 北京单日：`succeeded / degraded`，保存可读；仍有预约/开放时间等未核实信息。
- 上海两日：`needs_attention / draft`，真实路线不完整，保留缺口，不能记为完整生成成功。
- 单日局部改排：`needs_attention / draft`，独立草稿保存，不自动覆盖原版本；高德额度耗尽后的路线无法补验。
- 北京公开资料研究：返回 5 条有来源的证据，静态资料不是出行当日的事实证明。
- 一份合成单页 PNG：仅 1 次视觉调用，解析后状态为 `awaiting_review`；核对合成原文后另行发布，变为 `published`。直接读取 Chroma 确认 1 条向量、资料版本 2；没有用假解析结果替代真实视觉验收。

最终预发送计数：文本 **4/12**，视觉 **1/1**，embedding **14/50**，高德 **100/100**。失败尝试计入；类别独立封顶，Docker 重启未清零。高德及视觉额度已耗尽，不继续该类付费请求。北京/上海/改排按用户认可的“成功、降级或草稿契约”验收，但这不是三次完整规划通过。

## 数据与回滚位置（本机，不提交备份内容）

- 迁移前最终备份：`E:\project\trip-planner-backups\final-pre-java-20260915-1310`。
- 最初恢复验证备份：`E:\project\trip-planner-backups\pre-java-business-20260915-1210`。
- 切换后新架构备份：`E:\project\trip-planner-backups\post-java-20260915-1331`。
- 新架构恢复副本：`E:\project\trip-planner-backups\post-java-restored-20260915-1333`；恢复库 `java_restore_1cc0862ae000`，仅作核对。
- 旧镜像 `langchain-trip-planner-{backend,frontend}:pre-java-migration` 保留。旧库未被新 Java 使用；回滚按 [运行手册](../../operations/java-migration.md) 恢复成套旧数据，不连接新版业务库。

18 条旧行程原属已不存在的用户 ID 1，经用户确认原样保留，没有转给其他账号。其余 7 条原行程按原所有者读取、版本和 JSON 一致；2 个原文件 SHA-256 一致。账号身份/角色/哈希保留与签名令牌兼容已验证。用户已实际使用原账号登录并查看历史，反馈部分图片不可见；复核发现高德验收额度已满，以及 Java 错误缓存占位图 1 小时的问题。已修复占位图 `Cache-Control: no-store`，用户批准结束本轮验收并恢复日常调用，保留原账本、不再追加代理发起的付费测试。

用户刷新后的现有访问日志确认主景区图片已返回 36,396 字节，子点位最初仍为占位图。按用户要求统一更新图片链路，没有按景点名称硬编码：实际 ID 原图候选重试；无图时仅接受同城、3 公里内、名称前缀严格匹配的唯一景区候选，内嵌“景区参考图（非该具体点位实拍）”标注。图片需真实解码通过，拒绝损坏文件和远程 SVG；短失败缓存、有限容量成功缓存、24 秒截止时间传播；前端更新图片缓存版本，统一 8:5 铺满卡片，参考图标注不裁切。新增 12 项离线回退测试和 1 项损坏 JPEG 用例，保留原 HTTP 升级/格式识别检查。用户已确认图片正确显示，并反馈两侧留白；随后统一调整卡片比例，补充浏览器几何比例检查。日常 Agent/前端已重建；没有为图片追加代理发起的真实高德请求，用户刷新后“岳元帅”点位返回 49,043 字节的参考图，但不能声称每个点位都有实拍。

## 遇到的问题和修复

- Java HTTP 客户端升级尝试与 Uvicorn 不兼容：固定内部 HTTP/1.1。
- 首次远程 CI 的恢复演练只等待容器启动，紧随其后的资料复核请求遇到尚未就绪的入口：恢复后等待服务健康并核验 Nginx 的离线夹具接口，不把“启动了”当作“可接受业务请求”。
- 第二次 CI 的 `start --wait --wait-timeout` 命令立即失败，旧脚本未输出 stderr，不能据此断言服务故障或具体参数兼容原因。改用普通 `start` 和独立 Docker 健康检查轮询，再核验公开入口；失败显示 stderr，但不输出备份内容。增加 5 项等待/拒绝/错误诊断回归检查，恢复报告仅在源服务和入口恢复后生成。
- Spring SSE 格式与前端空格假设不一致、异步分派鉴权重复：兼容 SSE 可选空格，初始鉴权后允许异步分派，12 项浏览器复验通过。
- 发布失败重放可能重走图片解析：持久化作业阶段，重放发布只重试索引；新增测试。
- 真实图片解码缺少 Pillow：增加固定依赖及真解码、损坏图片、格式不符门禁测试；失败发生在视觉调用前。
- 恢复工具最初用 Docker CLI 读取 Compose 引号格式环境文件：改为 dotenv 解析和受控环境转发；新克隆复验通过，失败克隆保留。
- 公网依赖下载两次被截断，哈希校验正确拒绝：没有禁用校验，本机构建复用已验证依赖镜像并执行锁定版本校验/pip check。CI 仍从默认官方基础镜像构建。Maven 本机使用显式构建镜像参数，默认仓库仍为 Maven Central。

## 发布门槛

原账号实际登录确认、准确提交的 GitHub CI 是独立门槛；未满足不能宣告迁移交付完成。工作在 `codex/java-business-python-agent` 分支，仅提交本轮文件；保留用户未跟踪的 `backend/scripts/rag_current_validation.py` 和 `docs/evidence/rag-current-20260913/`，不纳入提交。完成后快进本地 `main`、推送 `origin/main`，不强推。
