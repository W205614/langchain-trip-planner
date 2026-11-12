# 贡献指南

欢迎提交问题、文档修正和代码改动。功能缺陷请在 [Issues](https://github.com/W205614/langchain-trip-planner/issues) 中说明复现步骤、预期结果、实际结果和版本；安全问题使用[私密漏洞报告](SECURITY.md)。

## 开发环境

仓库使用 Java 21 和 Node.js 22；Agent 镜像及 CI 使用 Python 3.13.18，宿主开发建议使用同一 Python 3.13 系列。仅运行首次部署配置生成器时支持 Python 3.11+。Docker Compose 用于 PostgreSQL、Mailpit、S3Mock 及完整浏览器集成测试。CI 的准确版本与执行顺序见 [ci.yml](.github/workflows/ci.yml)。

以下命令在仓库根目录使用 PowerShell 执行，Python 指向你自己的虚拟环境。先安装需要修改部分的依赖：

```powershell
python -m pip install -r backend/requirements.txt pytest
Push-Location frontend
try { npm ci } finally { Pop-Location }
```

## 按改动运行检查

Python 规则、Agent 或维护脚本改动，先运行直接相关的测试文件；完整检查为：

```powershell
Push-Location backend
try { python -m pytest -q -rs } finally { Pop-Location }
python backend/scripts/check_public_contract.py
```

pytest 配置会替换模型和地图凭据、隔离运行目录，默认不调用付费供应商。部分 Docker、restic 等集成用例按环境条件跳过，报告中需列出跳过项及原因。

Java 改动使用仓库的 Maven Wrapper。只运行单元测试时，可不设置 `TEST_DATABASE_URL`；账号、任务、权限、存储及迁移改动还需运行 PostgreSQL 集成测试。以下命令创建独立测试依赖，执行前确认本机 15439、11025、18025、19190 端口可用：

```powershell
docker compose -p trip-contrib -f docker-compose.public-validation.yml up -d --wait
$env:TEST_DATABASE_URL = 'jdbc:postgresql://localhost:15439/trip'
$env:TEST_DATABASE_PASSWORD = 'isolated-test-only'
$env:TEST_MAILPIT_URL = 'http://127.0.0.1:18025'
$env:TEST_SMTP_PORT = '11025'
$env:TEST_S3_ENDPOINT = 'http://127.0.0.1:19190'
Push-Location business-backend
try { .\mvnw.cmd -B -ntp test } finally { Pop-Location }
docker compose -p trip-contrib -f docker-compose.public-validation.yml stop
```

Linux 使用 `sh mvnw -B -ntp test`。测试数据库仅存放合成数据，与日常部署分离。按改动可用 `-Dtest=类名` 缩小范围；检查完成后停止自己创建的资源，保留需要排查的日志。

前端改动运行类型检查和构建：

```powershell
Push-Location frontend
try { npm run build } finally { Pop-Location }
```

浏览器回归需要运行中的离线验证栈；按 [CI 的 Browser recovery and isolation 步骤](.github/workflows/ci.yml)准备镜像、测试管理员和 Playwright Chromium，再在 `frontend` 执行 `npx playwright test --config playwright.config.ts`。公开邮箱流程使用独立栈，命令和端口见[公开部署手册](docs/operations/public-deployment.md#本地隔离验证)。

真实模型、高德、SMTP 或云存储联调单独安排凭据、目标与调用预算。常规测试和文档检查使用离线夹具。

已发布镜像与源码构建入口见 [README](README.md) 和[镜像分发手册](docs/operations/container-distribution.md)。维护者复核已有版本时可运行只读的 [Anonymous published container consumer](.github/workflows/consume-published-containers.yml)：验证工具绑定该次工作流提交，应用源码另从版本 Tag 解引用；不会重新发布镜像。

## 提交改动

- 保持改动集中，说明解决的问题、行为变化、已执行的检查及仍待完成的验证。
- 修改 API 时同步 [接口契约](contracts/README.md)和前端类型；数据库结构通过新增 Flyway 迁移扩展。
- 修改依赖时更新对应锁文件，沿用现有生成流程。安全门禁见 [security.yml](.github/workflows/security.yml)；告警复核规则见[安全复核手册](docs/operations/security-review-policy.md)。
- `.env`、运行配置、数据库、用户原件和未脱敏日志留在私有目录。历史验收原件及其哈希绑定保留原始内容，新结果写入新的批次。

提交前运行 `git diff --check` 并核对改动清单。Pull Request 描述中列出本地结果；CI 运行后检查当前提交对应的结果。

原创贡献沿用项目 [MIT License](LICENSE)。引入第三方代码、补丁或素材时注明来源、版本、修改及适用许可，并按需更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。依赖锁文件通过解析器生成，保留平台条件并核对差异；PDF 解析改动还应运行 `backend/tests/test_agent_extraction.py`，验证真实 PDF 渲染、输入限制和并发访问。
