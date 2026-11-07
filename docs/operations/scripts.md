# 服务端脚本索引

路径相对于仓库根目录。首次安装只需配置生成脚本；日常七服务操作见[单机运行手册](single-host.md)。本机备份已停用，备份条目用于识别维护工具。

## 配置、数据与维护（需选定部署及其私有配置）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [prepare_java_deployment.py](../../backend/scripts/prepare_java_deployment.py) | 新安装时拆分服务凭据；升级使用专用参数 |
| [deployment_assets.py](../../backend/scripts/deployment_assets.py) | 备份工具共用模块，读取 Docker 实际挂载、数据库与清单 |
| [archive_checks.py](../../backend/scripts/archive_checks.py) | 恢复路径共用模块，核对归档校验和 |
| [backup_java_deployment.py](../../backend/scripts/backup_java_deployment.py) | 维护窗口停止写入后，归档 Java 数据库与文件 |
| [restore_java_backup.py](../../backend/scripts/restore_java_backup.py) | 有成套备份时，恢复到隔离数据库与文件目录 |
| [check_backup_retention.py](../../backend/scripts/check_backup_retention.py) | 已有备份时检查清单并提出保留建议 |
| [enroll_dataset.py](../../backend/scripts/enroll_dataset.py) | 既有数据首次登记，核对停止写入的挂载与备份 |
| [existing_data_rehearsal.py](../../backend/scripts/existing_data_rehearsal.py) | 有私有备份时准备升级与回滚克隆演练 |
| [automated_maintenance.py](../../backend/scripts/automated_maintenance.py) | 检查指定项目的维护配置；执行备份或恢复调度需维护窗口 |
| [openai_check.py](../../backend/scripts/openai_check.py) | 检查官方模型访问；显式 `--live` 时用持久预算验证文本、视觉和 Embedding |
| [rag_migrate.py](../../backend/scripts/rag_migrate.py) | 管理员预览／续跑分代重建，旧索引保留 |
| [storage_objects.py](../../backend/scripts/storage_objects.py) | 成套快照导出 S3 文件及恢复后克隆库位置映射 |
| [restic_archive.py](../../backend/scripts/restic_archive.py) | 使用 restic 0.19.1 加密归档已校验备份，恢复到新目录 |
| [prepare_external_alerts.py](../../backend/scripts/prepare_external_alerts.py) | 生成私有 Alertmanager 邮件／Webhook 配置，不实际发送 |
| [public_edge_acceptance.py](../../backend/scripts/public_edge_acceptance.py) | 专用隔离栈验证 HTTPS、可信代理、CSRF、限流和告警投递故障恢复 |

## 构建与契约（按 Dockerfile 或契约更新流程调用）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [export_internal_contracts.py](../../backend/scripts/export_internal_contracts.py) | 内部模型变更时，导出受版本控制的接口 schema |
| [install_runtime_security_patches.py](../../backend/scripts/install_runtime_security_patches.py) | 构建 Debian 镜像时，安装固定版本及哈希的安全修补 |
| [restrict_runtime_tools.py](../../backend/scripts/restrict_runtime_tools.py) | 构建时限制 CLI，并在检查模式核对运行能力 |

## 隔离环境与 HTTP 验收（需要专用测试账户、服务或夹具）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [validation_server.py](../../backend/scripts/validation_server.py) | 离线验证栈的 HTTP 供应商夹具 |
| [validation_research_provider.py](../../backend/scripts/validation_research_provider.py) | 浏览器验证栈的离线检索与问答夹具 |
| [optimization_seed_admin.py](../../backend/scripts/optimization_seed_admin.py) | 初始化隔离夹具专用的合成管理员 |
| [smoke_http.py](../../backend/scripts/smoke_http.py) | 经 Nginx 验证合成上传与入口边界 |
| [java_api_contract_smoke.py](../../backend/scripts/java_api_contract_smoke.py) | 验证 Java 账号与偏好接口 |
| [java_knowledge_smoke.py](../../backend/scripts/java_knowledge_smoke.py) | 使用解析与索引夹具验证资料审核生命周期 |
| [java_fault_smoke.py](../../backend/scripts/java_fault_smoke.py) | 注入跨服务故障，检查隔离栈的错误行为 |
| [lifecycle_smoke.py](../../backend/scripts/lifecycle_smoke.py) | 重启测试容器，检查任务生命周期 |
| [business_benchmark.py](../../backend/scripts/business_benchmark.py) | 运行完整 HTTP 业务契约评测 |
| [assistant_proposal_acceptance.py](../../backend/scripts/assistant_proposal_acceptance.py) | 验证助手提案从生成到确认的 HTTP 流程 |
| [final_acceptance.py](../../backend/scripts/final_acceptance.py) | 用真实 Java 和 PostgreSQL 验证角色权限与规划流程 |
| [final_scenarios.py](../../backend/scripts/final_scenarios.py) | 通过业务 HTTP 验证核心规划场景，供应商使用离线夹具 |
| [final_fault_acceptance.py](../../backend/scripts/final_fault_acceptance.py) | 检查故障下的任务生命周期和授权 |
| [optimization_http_comparison.py](../../backend/scripts/optimization_http_comparison.py) | 复现冻结的开发反例，比较业务接口结果 |
| [optimization_fault_drill.py](../../backend/scripts/optimization_fault_drill.py) | 在专用候选栈执行故障演练 |
| [travel_holdout_acceptance.py](../../backend/scripts/travel_holdout_acceptance.py) | 用冻结留出集一次性验证隔离业务 HTTP |
| [java_recovery_drill.py](../../backend/scripts/java_recovery_drill.py) | 将测试数据库与上传文件恢复到独立容器 |
| [performance_drill.py](../../backend/scripts/performance_drill.py) | 在验证栈做有上限的负载与依赖故障演练 |

## 真实供应商验证（需独立授权、可用凭据和调用预算）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [check_amap_mcp.py](../../backend/scripts/check_amap_mcp.py) | 单独探测高德 MCP 连通性 |
| [live_model_sample.py](../../backend/scripts/live_model_sample.py) | 调用真实供应商生成有界样本，无 Java 业务写入 |
| [java_live_acceptance.py](../../backend/scripts/java_live_acceptance.py) | 使用持久账本经 Java 执行可续跑验收 |
| [live_project_acceptance.py](../../backend/scripts/live_project_acceptance.py) | 通过已部署的 Java 到 Agent 链路做有界 HTTP 验收 |
| [final_live_acceptance.py](../../backend/scripts/final_live_acceptance.py) | 在既有持久验收批次内继续真实供应商抽样 |

## 离线评测与证据（使用指定数据集或账本）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [time_window_benchmark.py](../../backend/scripts/time_window_benchmark.py) | 用合成排程输入比较时间窗口求解结果 |
| [improvement_benchmark.py](../../backend/scripts/improvement_benchmark.py) | 在固定开发集或留出集上比较排程；文件字节受保护 |
| [prepare_blind_review.py](../../backend/scripts/prepare_blind_review.py) | 从离线样本制作待人工评分的评审表 |
| [optimization_status.py](../../backend/scripts/optimization_status.py) | 更新指定批次的范围和验收状态账本 |

## 监控与通知（需要对应监控配置）

| 脚本 | 何时使用、检查或产出什么 |
|---|---|
| [notification_receiver.py](../../backend/scripts/notification_receiver.py) | 验证环境专用的有界通知记录端点 |
| [notification_smoke.py](../../backend/scripts/notification_smoke.py) | 停止测试后端，检查 Prometheus 到本地接收端的告警链路 |
| [operational_alert_receiver.py](../../backend/scripts/operational_alert_receiver.py) | 单机部署的持久化本地告警收件箱 |

## 整理清单（2026-11-05）

| 核对对象 | 引用与处置 |
|---|---|
| `final_acceptance.py` | CI 直接调用，保留离线角色与规划验收入口。 |
| `final_live_acceptance.py`、`live_project_acceptance.py` | 源码与历史记录引用；前者受既有验收批次约束，后者验证已部署的 Java → Agent 链路。保留两者，运行前核对对应账本与目标环境。 |
| `notification_receiver.py`、`operational_alert_receiver.py` | Compose 引用，分别服务测试与持久化告警收件箱。 |
| `improvement_benchmark.py`、历史证据目录 | 有冻结字节约束与历史核验用途，保留原路径。 |
| 七服务日常操作章节 | 从迁移手册提取到单机手册，旧章节保留导航。 |

核对范围包括工作流、仓库 Compose、源码、文档及本机当前 daily-release Compose。私有 Compose 通过服务镜像运行，脚本名检索无法覆盖镜像内所有调用。本次没有确认可删除的跟踪文件。
