# 新公开环境部署与外部联调

本指南针对新的单机 Linux／Docker 主机。已有日常环境沿用原挂载和数据集登记，不能照抄新建配置覆盖。只支持当前已实现的 SMTP、S3、PostgreSQL 和容器接口集合，真实云厂商须运行对应联调命令。

以下命令从仓库根目录、实际 Docker 主机执行。配置与告警生成工具需要固定的主机依赖：

```powershell
python -m pip install python-dotenv==1.2.3 PyYAML==6.0.3
```

执行本地 CA／客户端证书维护时另装 `cryptography==50.0.1`；主机模型检查使用模型手册中的完整锁定依赖。Linux 在准备含秘密的源 `.env`、证书和生成配置之前执行 `umask 077`，并使用私有父目录；Windows 使用仅操作者和系统可访问的父目录 NTFS ACL，不能把 POSIX mode 当作 NTFS 保密保证。

Compose 的 `--env-file` 供变量插值，服务自己的 `env_file` 才给应用传参：`public.env` 保存入口／挂载配置，`business.env` 保存 SMTP／S3／数据库配置，`agent.env` 保存 OpenAI／高德配置。某项写入 `backend/.env` 后，还需要生成／升级对应运行文件并重建容器，才会成为应用的实际环境。

## HTTPS 与邮箱模式

准备域名 A/AAAA、开放的 80/443 端口和持久磁盘。在私有 `backend/.env` 配置 OpenAI、高德和以下项目：

```dotenv
PUBLIC_BASE_URL=https://trip.example.com
ACME_EMAIL=admin@example.com
SMTP_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-private-password
SMTP_FROM=no-reply@example.com
```

发件域验证、DNS 邮件记录和供应商额度由实际邮件服务配置；本项目不声称已验证任意发件域或邮件必达。

```powershell
python backend/scripts/prepare_java_deployment.py --public --output deploy/runtime-production
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml config --quiet
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml up -d --build --wait
```

首次准备不覆盖既有目录。生成的私有配置启用生产校验、邮箱验证、管理员 MFA 和所有付费入口的调用身份要求。管理员通过已有 `BOOTSTRAP_ADMIN_USERNAME` 配置登记，登录后先完成 MFA 和邮箱绑定。

新配置将 `DATA_DIR=/app/runtime`、`CHROMA_DIR=/app/data/chroma` 分别写入对应持久卷；Agent 根文件系统保持只读。升级命令不会改写已有 `DATA_DIR`，已有环境切换生产 Compose 前，应在停写备份后把原运行数据和数据集标记迁移至相应卷，再设置路径，不能只改路径而遗漏原数据。

Caddy 自动申请和续期证书，证书卷需持久化。只有 Caddy 公开入口，前端、业务、数据库和监控端口不对外发布。`PUBLIC_EDGE_CIDR` 默认 `172.30.244.0/28`，部署前确认与主机现有网络不重叠；如改变，前端信任范围与网络同时由该配置生成。前端只信任隔离 edge 网络转发的客户端地址和协议，入口清除内部服务身份头。

当前公网模板固定发布80／443；`PUBLIC_BASE_URL` 应使用与443入口一致的 HTTPS 来源，不带路径或自定义端口。`config --quiet` 只检查合并配置，不能证明 DNS、端口可达、证书已签发或账号可用。

入口使用 `deploy/images/caddy.Dockerfile` 固定官方 Caddy 2.11.9 源码与标准模块，生成 `trip-planner-caddy:2.11.9-security1`。固定的 `caddy.go.mod`／`caddy.go.sum` 将 Smallstep Crypto 升至 0.92.0，配套固定证书库的[官方兼容迁移提交 cddde30af36e](https://github.com/smallstep/certificates/commit/cddde30af36e238f156bb557bfd1f11a05ffe6f9)（使用已修复的 Go JOSE v4，移除链接的 v3），并将 `x/net` 升至 0.62.0 修复 GO-2026-6860；不修改 Caddy 业务源码或关闭 ACME。构建检查最终 Go 模块清单，CI 扫描真实镜像。`PUBLIC_CADDY_IMAGE` 仅用于指定已构建、已扫描的同一派生版本。依据见 [Caddy 2.11.9 发布记录](https://github.com/caddyserver/caddy/releases/tag/v2.11.9)、[Smallstep Crypto 0.92.0](https://github.com/smallstep/crypto/releases/tag/v0.92.0) 与 [Go 官方漏洞记录](https://pkg.go.dev/vuln/GO-2026-6860)。

上线核对：通过 HTTPS 完成注册、收信、验证、重新登录；检查会话 Cookie 为 Secure／HttpOnly，未验证账号的 AI 请求被拒绝；从公网请求 `/internal/`、`/metrics`、`/actuator/` 必须不可读。两名客户端的请求限额应独立，伪造 `X-Forwarded-For` 不应改变客户端身份。

## S3 与云 PostgreSQL

对象服务配置及原文件迁移见 [对象存储手册](object-storage.md)。S3 凭据只授予指定 Bucket／前缀的读写、列举和删除权限；Bucket 保持私有。上传、下载、解析、删除和恢复均走同一适配层，不能仅通过配置修改旧文件所属端点。

云数据库沿用私有 `business.env` 的 `JDBC_DATABASE_URL`、`POSTGRES_USER`、`POSTGRES_PASSWORD`，连接池使用 `DB_POOL_MAX_SIZE`（默认 12）、`DB_POOL_MIN_IDLE`（默认 4）。例如 TLS 地址为 `jdbc:postgresql://db.example.com:5432/trip?sslmode=verify-full&sslrootcert=/run/certs/postgres/ca.crt`。将供应商 CA 放入专用目录，并设置 `TRIP_POSTGRES_CA_DIR` 为该目录的绝对路径。

新云库安装在公开 Compose 命令最后追加 `-f docker-compose.cloud-db.yml`，此配置停用内置 PostgreSQL 的自动启动，移除对它的启动依赖，并只读挂载 CA 目录。只启动所需服务，不选择 `local-database` profile。真实证书、DNS、账号权限、数据库版本和连接限额需在选定平台联调；生产数据库仅允许业务主机访问。已有数据库切换涉及数据迁移与数据集身份检查，不能把空云库地址直接填入旧环境。

在已生成的 `deploy/runtime-production/business.env` 中填写真实 `JDBC_DATABASE_URL`／`POSTGRES_USER`／`POSTGRES_PASSWORD`，数据库名称以 JDBC URL 为准。另在 **`deploy/runtime-production/public.env`** 加入挂载目录，例如 Linux：

```dotenv
TRIP_POSTGRES_CA_DIR='/srv/trip-private/postgres-certificates'
```

Windows Docker 主机使用其实际目录，例如 `E:/trip-private/postgres-certificates`。目录内文件名须与 JDBC URL 的 `/run/certs/postgres/...` 一致。然后执行完整参数，避免误用默认四服务项目：

```powershell
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml -f docker-compose.cloud-db.yml config --quiet
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml -f docker-compose.cloud-db.yml up -d --build --wait backend agent frontend edge
```

成功判据：backend 就绪、Flyway 在预期真实库完成，实际账号写入／读回成功，且该库会话使用 TLS；在隔离联调目标以错误 CA／主机名负例确认未跳过验证，再执行成套备份与独立恢复。仅容器 healthy 不证明连接的是预期云库，不能用本地库的表或健康状态替代核对。

成套备份依据 backend 实际 JDBC 连接选择源库，支持没有内置 PostgreSQL 的项目；参数、TLS／客户端证书、停写窗口和独立恢复边界见 [外部 PostgreSQL 备份](cloud-postgresql-backup.md)。

## 外部告警

私有配置增加 `ALERT_EMAIL_TO`，或配置 `ALERT_WEBHOOK_URL` 与可选 `ALERT_WEBHOOK_TOKEN`。邮件告警使用 STARTTLS SMTP。生成配置不会发送通知：

```powershell
python backend/scripts/prepare_external_alerts.py --env-file backend/.env --output deploy/runtime-production/alertmanager.yml
```

Linux 主机还需将这个 `0600` 私有文件交给维护版 Alertmanager 的运行身份，随后再启动容器；不要将含邮件密码／Webhook 令牌的配置改成全局可读：

```bash
sudo chown 65534:65534 deploy/runtime-production/alertmanager.yml
```

将 `TRIP_ALERTMANAGER_CONFIG` 设置为生成文件的绝对路径，然后使用同一公开 Compose 命令加 `--profile monitoring` 启动。实际发送前用 Alertmanager 自带 `amtool check-config /etc/alertmanager/alertmanager.yml` 验证；通过隔离告警或供应商测试收件地址核对 firing、resolved、去重和失败日志。本地收件箱继续保留，不能把本地落盘当作外部送达。

必须将该变量写入 **`deploy/runtime-production/public.env`**（Compose 插值源），指向刚生成文件的真实绝对路径；仅写进 `business.env` 或 `backend/.env` 不会改变监控挂载。例如在 Linux 使用 `realpath deploy/runtime-production/alertmanager.yml` 查得路径后加入该文件：

```dotenv
TRIP_ALERTMANAGER_CONFIG='/your/actual/project/deploy/runtime-production/alertmanager.yml'
```

Windows 可用 `(Resolve-Path deploy/runtime-production/alertmanager.yml).Path` 查得路径，并改为正斜杠。使用当前部署相同的全部 `-f` 参数（云数据库项目继续加 cloud-db overlay）检查并启动监控：

```powershell
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml --profile monitoring config --quiet
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml --profile monitoring up -d --build prometheus alertmanager alert-receiver
docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml --profile monitoring exec -T alertmanager /bin/amtool check-config /etc/alertmanager/alertmanager.yml
```

告警邮件适配器只支持 STARTTLS。业务 SMTP 若使用465端口隐式 TLS，应为告警准备单独的私有 env 并用 `--env-file` 传给生成脚本，或仅配置 HTTPS Webhook；不要把 `SMTP_SECURITY=tls` 当作告警模板已支持。成功判据为原生校验通过、测试 firing／resolved 到达指定接收端、模拟失败可见于 Alertmanager 日志／失败计数；仅生成文件或本地 inbox 落盘不算外部接受。完整隔离验证命令见本文末尾，真实接收端仍需独立联调。

## 备份与恢复

本机原备份任务仍停用，没有因代码交付自动获得新恢复集。以下能力需在选定新目标、明确维护窗口后启用；不自动安装计划任务或清理历史快照。

先使用现有成套维护工具在停写窗口生成并验证备份。S3 模式额外导出被数据库引用的原文件、保留迁移副本和未完成预留的状态，写入同一个校验清单。参考 [现有维护流程](optimization-governance.md) 与 [对象存储恢复](object-storage.md)。

备份自动保存所选应用容器的实际环境（云库项目可没有本地 postgres）。公开安装还应传 `--private-config-dir E:\trip-private\runtime-production`，将 `public.env`、外部告警 YAML 及集中放置的私有配置一并归档到 `private-configuration.tar`；此参数必须指向实际集中保存这些文件的目录，而非照抄示例空目录，且不能包含备份输出或符号链接。恢复工具核验这份归档但不自动启用原域名、云地址或告警收件人，操作员在隔离恢复后从中恢复选定配置。证书持久卷仍由 Caddy 保存，重建入口时可向 ACME 重新签发；CA、客户端证书和归档解密密码须另有安全副本。

安装官方 restic **0.19.1**，校验官方发布哈希。建立单独私有配置，例如 `E:\trip-private\restic.env`：

```dotenv
RESTIC_REPOSITORY=s3:https://s3.example.com/trip-backups
RESTIC_PASSWORD_FILE=E:/trip-private/restic-password.txt
AWS_ACCESS_KEY_ID=backup-account
AWS_SECRET_ACCESS_KEY=private-backup-secret
AWS_DEFAULT_REGION=your-region
```

归档密码独立保管，不与归档放在同一故障域；备份中含数据库和私有配置，必须保护该配置与密码。工具仅接收已经通过成套清单校验的备份：

```powershell
python backend/scripts/restic_archive.py init --env-file E:\trip-private\restic.env --execute
python backend/scripts/restic_archive.py archive --env-file E:\trip-private\restic.env --source E:\trip-private\fresh-backup
python backend/scripts/restic_archive.py archive --env-file E:\trip-private\restic.env --source E:\trip-private\fresh-backup --execute
python backend/scripts/restic_archive.py check --env-file E:\trip-private\restic.env
python backend/scripts/restic_archive.py restore --env-file E:\trip-private\restic.env --target E:\recovery\trip-new-restore --execute
```

恢复目标必须全新且在仓库之外。restic 恢复、文件哈希正确和应用恢复演练是三个结论：取回成套目录后仍须运行 `restore_java_backup.py`，S3 还需明确的隔离目标配置。不会覆盖原数据库或原 Bucket。`check` 默认只检查仓库结构，不证明读遍所有数据，也不证明故障域独立。

保留期限、异机访问策略和定时调度由运营配置；备份不实时反映账号注销。恢复旧备份后须重新应用后续删除状态，避免重新公开已撤回资料。公开运营前应完成一次所选真实目标的备份、隔离还原和业务读回。

## 本地隔离验证

`docker-compose.public-validation.yml` 仅提供 PostgreSQL、Mailpit 和 S3Mock 测试依赖；Java 测试使用 `TEST_DATABASE_URL`、`TEST_MAILPIT_URL`、`TEST_SMTP_PORT` 和 `TEST_S3_ENDPOINT`。这些替身不对外投递邮件，不加载日常密钥或业务卷。

完整浏览器栈为 `docker-compose.public-e2e.yml`，默认使用三个 `trip-public-validation-*` 测试镜像（也可用 `PUBLIC_VALIDATION_*_IMAGE` 指定当前已构建候选）。启动后入口 `http://127.0.0.1:18881`，测试收件箱 `http://127.0.0.1:18026`；设置相应 `PUBLIC_E2E_BASE_URL`、`TEST_MAILPIT_URL` 执行 `public-operations.spec.ts`。该栈的模型和地图为离线夹具，真实模型必须单独验收。

HTTPS／代理与外部告警使用另一独立项目 `trip-public-edge`，不复用上述项目或日常环境的卷。先准备三份当前候选测试镜像；监控镜像复用 `deploy/images` 的固定版本安全构建。Python 使用 `backend/requirements.lock` 中的依赖（包括 `cryptography`、PyYAML、python-dotenv）。检查 `10.253.245.0/28` 无现有网段冲突后执行；如有冲突，通过 `PUBLIC_EDGE_TEST_CIDR` 同时调整 Nginx 信任范围和 edge 子网。

```powershell
python backend/scripts/public_edge_acceptance.py prepare
docker compose -p trip-public-edge -f docker-compose.public-edge-validation.yml build edge
docker compose -p trip-public-edge -f docker-compose.public-edge-validation.yml up -d --no-build --wait postgres backend agent amap-fixture frontend webhook edge mailpit
python backend/scripts/public_edge_acceptance.py trust
docker compose -p trip-public-edge -f docker-compose.public-edge-validation.yml up -d --build --wait --wait-timeout 120 alertmanager prometheus
python backend/scripts/public_edge_acceptance.py https
python backend/scripts/public_edge_acceptance.py alerts
docker compose -p trip-public-edge -f docker-compose.public-edge-validation.yml stop
```

可用 `PUBLIC_EDGE_CADDY_IMAGE`、`PUBLIC_EDGE_ALERTMANAGER_IMAGE`、`PUBLIC_EDGE_PROMETHEUS_IMAGE` 指向已经构建的相同版本镜像，省略对应构建并使用 `--no-build`。测试入口限定 `https://localhost:18443`、HTTP 跳转端口 `18082`、Mailpit `18028`。只复制 Caddy 内部 CA 到被 Git 忽略的 `.cache/public-edge-validation/tls`，不安装主机根证书。临时 SMTP 证书有效期两天；过期后先停止该测试栈，将旧私有目录归档到仓库外，再重新准备。测试专属 Caddyfile 引用生产入口配置，额外的 `edge` 站点只服务内部 Webhook。

上面的命令示例适用于本地 Windows Docker Desktop；Linux 在 `prepare` 之后还须执行 `sudo chown 65534:65534 .cache/public-edge-validation/alertmanager.yml`。CI 已包含该步骤，私有文件保持 `0600`。

`https` 创建隔离账号，核验跳转、HSTS、Secure／HttpOnly／SameSite、CSRF、内部路由隔离、可信代理和不同实际客户端的限流。`alerts` 使用真实 Prometheus 探针和原生 Alertmanager，通过 STARTTLS SMTP 及带 Bearer 的 HTTPS Webhook 验证触发、恢复、投递失败计数增加及恢复后的自动重试，同时检查本地收件箱去重；会短暂停止并恢复**本项目** Mailpit。测试配置将分组等待缩短为 1 秒、分组间隔缩短为 2 秒，生产模板仍为 10 秒／2 分钟。

成功判据为两个脚本退出码均为 0，私有 `https-result.json` 与 `alerts-result.json` 均生成；失败时查看本项目 `backend frontend edge alertmanager prometheus mailpit` 日志，并始终执行 `stop`。Mailpit 接受代表测试 SMTP 接受，Webhook 2xx 代表接收端接受；均不证明真实用户收件。2026-11-07 原始隔离验收见 [历史入口记录](../evidence/public-edge-20261107/report.json)，最终 `2.11.9-security1` 镜像复测和扫描见 [Caddy最终证据](../evidence/caddy-security-20261107/report.json)。公网 DNS、ACME 申请／续期、真实邮件供应商及真实 Webhook 的接入仍待所选环境验证。

CI 的 Docker 验收 job 在公开账号浏览器流程后运行同一隔离验证，先检查子网和宿主端口，再保留结果与容器日志，始终停止该验证项目。当前本地通过不代表尚未推送的 GitHub 工作流已执行。

代理头处理依据 [Caddy reverse_proxy 官方文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)，SMTP／Webhook 配置及投递行为依据 [Alertmanager 官方配置](https://prometheus.io/docs/alerting/latest/configuration/)。
