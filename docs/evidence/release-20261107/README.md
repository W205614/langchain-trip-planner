# v0.1.0 发布检查

检查日期：2026-11-07（Asia/Shanghai）。发布范围为现有公开仓库的源码版本；不发布 Packages、容器镜像或生产部署。Tag 是最终发布提交的入口，测试与安全工作流必须在该提交完成后核对，不能用历史绿色状态代替。

## 本次改动

- 原创代码与已授权截图采用 MIT，署名 W205614；补齐仓库直接保存的上游代码与补丁许可通知，见[第三方说明](../../../THIRD_PARTY_NOTICES.md)。第三方许可不被 MIT 替代，既往提交的授权与 PyMuPDF 使用另行核对。
- PDF 资料渲染从 PyMuPDF 改为固定版本 `pypdfium2==5.15.0`，继续使用现有 Pillow。保留 1–10 页、20 MiB 下载及每页 2400 万像素限制；按实际渲染尺寸检查像素数。PDFium 调用串行化，页面、位图和文档显式关闭，模型调用在锁外。
- 补充真实双页 PDF、损坏 PDF、页数与像素限制、4 线程 8 次解析回归。行程改排测试显式排除本机业务数据库环境变量，避免本地 `.env` 干扰 Agent 启动边界。
- 缩小 Docker 上下文：Java 排除 `target`、私有环境文件和日志；Agent 排除私有环境、旧 runtime 与锁文件；前端排除本地覆盖配置，保留部署流程所需的浏览器公开地图配置。

## 验证方法

本机为 Windows / Docker Desktop Linux 引擎。Python 使用 CPython 3.11.18 构建镜像；Java 使用 JDK 21、仓库 Maven Wrapper；前端使用现有 Node.js 环境，CI 另使用 Node.js 22。所有离线业务测试使用新建的专用容器、数据库及合成账号，不加载日常环境文件或数据挂载。

| 检查 | 方法与边界 |
|---|---|
| Python | `python -m pytest -q -rs`，运行既有测试与新增 PDF 回归；缺少 restic 与显式原生检查条件时报告跳过，原生 ABI 单独在最终镜像验证 |
| Java | 独立 PostgreSQL、Mailpit、S3Mock；`mvnw.cmd -B -ntp test`，包括真实数据库事务、V1–V20 迁移、账号撤销竞争和对象存储接口 |
| 前端 | `npm ci` / `npm run build`；Playwright 使用离线验证栈，测试普通用户与合成管理员 |
| HTTP | `smoke_http.py`、`business_benchmark.py`、`final_acceptance.py`、`final_scenarios.py`，先验证离线标记再操作测试账号 |
| 安全 | Gitleaks 检查全历史及跟踪源码；npm audit、pip-audit、Maven CycloneDX/OSV、实际镜像扫描、四语言官方 CodeQL；保留原始告警及适用条件 |
| 干净源码 | 从提交导出不含私有配置的副本，复验安装、构建、公开契约及关键 PDF/行程回归；镜像构建记录提交标签 |

本机详细输出保存在忽略的 `.codex-release-20261107/`；合成管理员凭据留在脚本约定的私有目录，不进入 Release。最终结果和 GitHub 链接以 Release Notes 为准。

## 文件与 Docker 整理

初始盘点涵盖 29,034 个本地文件、2,450,605,025 字节，包括未跟踪及忽略内容；未遍历 `.git` 内部或链接到外部目录。Git 初始工作区干净。备份、环境配置、数据库、上传原件、索引、预算账本及历史证据均保留。

两组 Go 模块下载分片共 98 个文件、102,528,849 字节，已与保留的完整 ZIP 做拼接 SHA-256 比较及 ZIP CRC 校验后移入 Windows 回收站。目录为 `.codex-closure/prometheus-316/prefetch-parts/` 下的 `google.golang.org/api/v0.305.0` 和 `github.com/oracle/oci-go-sdk/v65/v65.127.0`；恢复可从回收站取回，或按原字节范围从完整 ZIP 重建。项目目录占用减少约 97.8 MiB，不表示 Windows 磁盘空间已释放。

现有日常七服务及其四个命名卷、绑定目录与恢复点保留，自动备份任务不启用。验证使用独立 Compose 项目；结束后停止测试服务。共享构建缓存、其他项目资源和数据卷不清理。首次发布不因镜像停止或 dangling 状态删除镜像；已有恢复镜像按既有恢复点保留。

## 升级与已知限制

本次没有新增数据库迁移，仍为 V20。已有部署先按[迁移手册](../../operations/java-migration.md)确认成套备份与隔离恢复，再使用 Tag 对应源码构建受影响的 Agent；保留原私有配置和挂载。切换旧镜像不能替代数据库恢复。

模型、高德及云平台的真实凭据不用于本轮离线回归；真实资料质量抽样沿用有明确提交和日期的[行程事实核验记录](../itinerary-facts-20261107/report.md)，不冒充本次真实调用。SMTP、S3、云 PostgreSQL、DNS、正式 HTTPS 入口、外部告警仍需在实际平台验收。多实例高可用、预订支付和生产容量验证不在此次范围内。前端较大分块警告保留，未因发布进行无关重构。

Python 审计的 Chroma 原始告警必须保留：只在既有本地嵌入式、无远端 Chroma API 的源码与运行约束保持成立时应用仓库评估。新增或变化告警仍阻断发布。安全政策中的私密报告入口已通过 GitHub API 核实启用。
