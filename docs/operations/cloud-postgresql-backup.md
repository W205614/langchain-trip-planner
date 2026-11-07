# 外部 PostgreSQL 的成套备份与隔离恢复

备份从所选项目 **backend 容器的实际环境**读取 `JDBC_DATABASE_URL`、`POSTGRES_USER`、`POSTGRES_PASSWORD`。数据库 dump、表内容摘要、列清单与 S3 原件引用均通过同一个 PostgreSQL 客户端访问该目标；存在但没有被 JDBC 指向的本地 `postgres` 不参与选库。项目可以没有本地 PostgreSQL 服务，其他脚本默认的四服务清点契约保持不变。

主机工具先安装固定依赖（客户端证书转换使用 cryptography，工具本身不在主机调用模型）：

```powershell
python -m pip install python-dotenv==1.2.3 cryptography==50.0.1
```

实际数据库配置写入所选运行目录的 `business.env`；CA挂载目录变量写入 Compose读取的 `public.env`。完整 cloud-db overlay 命令见 [公开部署](public-deployment.md#s3-与云-postgresql)，仅把云地址写进源 `backend/.env` 不会改变已创建容器。

备份客户端镜像与应用部署共用 `deploy/images/postgres.Dockerfile`。独立连接云数据库前，在仓库根目录构建一次：

```powershell
docker build -f deploy/images/postgres.Dockerfile -t trip-planner-postgres:17.11-security1 deploy/images
```

## 支持范围

- PostgreSQL **17.x**；客户端使用 `trip-planner-postgres:17.11-security1`（官方 PostgreSQL17.11，刷新 Alpine 签名安全包），实际镜像 ID 写入清单，恢复也使用这份 PostgreSQL17 镜像。
- 显式单主机 URL：`jdbc:postgresql://host[:port]/database`。IPv4、普通 DNS 名、无 scope 的 IPv6 地址；数据库与用户为不超过63个字符的简单 ASCII 名称。
- 凭据仅使用独立 `POSTGRES_USER`／`POSTGRES_PASSWORD`，不接受 URL 中的用户名、密码、userinfo 或重复参数。
- URL 参数仅支持 `sslmode`、`sslrootcert`、`sslcert`、`sslkey`、`connectTimeout`（1–300秒）和 `ApplicationName`。未知参数明确失败，不做静默忽略或连接降级。
- 外部目标必须明确使用 `sslmode=verify-full` 与 `sslrootcert`。CA、可选客户端证书与私钥必须位于 backend **实际只读挂载**中，使用容器内绝对路径；符号链接不受支持。
- 客户端证书为 PEM，私钥为未加密 PKCS8 DER（JDBC 常用 `.pk8`）或 PEM；工具保留原始私钥副本，并为 libpq 转换成 PEM。PKCS12、私钥密码回调及自定义 SSL 工厂不在支持范围。
- backend 连接单一 Docker 网络，使用该网络的标准 DNS；会改变目标的自定义 DNS／host 映射及 legacy links 不在支持范围。客户端不加入其他网络，不发布端口，不初始化数据库服务器。

示例私有 `business.env`：

```dotenv
JDBC_DATABASE_URL=jdbc:postgresql://db.example.com:5432/trip?sslmode=verify-full&sslrootcert=/run/certs/postgres/ca.crt&sslcert=/run/certs/postgres/client.crt&sslkey=/run/certs/postgres/client.pk8
POSTGRES_USER=trip_app
POSTGRES_PASSWORD=your-private-password
```

只需要 CA 时省略 `sslcert`／`sslkey`。JDBC 与 libpq 都执行证书验证，不以错误证书、非验证模式或切回本地数据库作为回退。Linux 客户端私钥必须满足 PostgreSQL 权限要求；只读挂载不会自动修正权限。Windows Docker Desktop 的 NTFS 绑定通常显示 `0777`，JDBC 会拒绝这种私钥；需要预先配置具有正确 UID／`0600` 权限的 Linux 证书卷，再以只读方式挂给 backend。本轮验证实际使用了这种只读证书卷。

以下配置会改变 Spring 的实际数据源，因此当前适配器拒绝：`SPRING_DATASOURCE_*`、`SPRING_APPLICATION_JSON`、`SPRING_CONFIG_*`、`SPRING_PROFILES_*`，以及容器启动参数／JVM 选项中的数据源或额外配置覆盖。普通 `JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=...` 不受影响，但它不能代替 URL 中显式的 `sslrootcert`。额外 Spring 配置、多主机、代理选择目标、URL 动态认证及其他云 API 暂不支持。

## 同一命令执行备份

先完成实际平台的 TLS 和数据库权限联调。停写窗口必须覆盖使用该数据库和对象前缀的全部写入者；工具检查所选 backend／agent 已停止，其他主机和应用的写入者由操作员停止。

```powershell
# 在当前部署使用的完整 Compose 参数后执行 stop backend agent。
python backend/scripts/backup_java_deployment.py --project trip-production --output E:\trip-private\cloud-backup-20261107 --private-config-dir E:\trip-private\runtime-production
```

本地部署仍使用同一命令；本地 PostgreSQL 必须已经运行，JDBC 指向项目 PostgreSQL 的网络名称。工具不猜测数据库名称，也不会因外部连接失败改用本地库。

运行前确保固定客户端镜像已可用。工具创建独立的 `trip-backup-pg-client-*`，将 **仅 PG 连接设置**投影到私有 env，映射只读证书，使用只读事务默认值。不会把 JWT、模型密钥或其余 backend 环境传给数据库客户端。成功及失败路径均停止该客户端并保留容器；客户端环境含数据库凭据，应按后端容器相同的 Docker 管理权限保护。

Linux 上新建成套备份根目录、runtime 和数据库客户端 staging 为 `0700`，私有 env／私钥为 `0600`。Windows 依赖事先选定的私有父目录 NTFS ACL，脚本继承该 ACL。不要使用对 Everyone／普通用户组开放的父目录。中断产物保留，失败目录不会被当作完整备份；重试选择新的目录。

`manifest.json` 的 `database_provenance` 记录实际主机、端口、库、用户、TLS、服务器版本、客户端镜像和容器，不包含密码。外部目标的 `postgres_mount` 为 null；`images.postgres` 保存独立恢复所需的 PostgreSQL 客户端／服务器镜像身份。

当前停用中的 `automated_maintenance.py` 仍是本地四服务停启／heartbeat 流程。云数据库使用上述手动停写备份命令，不直接启用旧计划任务。计划备份没有因本功能交付而启用。

## 隔离恢复

校验清单后，使用既有 `restore_java_backup.py` 恢复到全新目录。包含 S3 快照时，必须显式配置新的 Bucket／前缀／来源，不能写回源对象位置。详见 [对象存储恢复](object-storage.md)。

```powershell
python backend/scripts/restore_java_backup.py --backup E:\trip-private\cloud-backup-20261107 --output E:\trip-recovery\cloud-check --object-config E:\trip-private\restore-object-target.env
```

恢复工具创建全新 PostgreSQL，使用独立 `trip`／`trip_restore` 凭据，并覆盖原 JDBC 地址。备份专用 `PGHOST`／TLS env 不会被加载到恢复数据库。原云数据库和源对象不会成为恢复写入目标；原云连接配置、证书与私有配置保留在私有备份中，供后续操作员选择性恢复。

## 成功判据与证据

2026-11-07 的隔离验证使用独立 TLS PostgreSQL 与不同库名／用户的本地 decoy：外部备份47表，S3 引用及68字节原件来自同一外部库；错误 CA／主机名均拒绝。全新数据库与 S3 目标恢复后，Java 就绪、外部库标记读回正确、本地 decoy 未变，Java 原件读取 SHA-256 一致。Linux `0700` 和 Windows 私有父目录 ACL 均分别验证，详见 [机器证据](../evidence/cloud-postgresql-backup-20261107/report.json)。真实云数据库还需在选定平台完成同一套 TLS、备份和恢复联调。

参数映射依据 [pgJDBC 连接参数](https://jdbc.postgresql.org/documentation/use/)、[libpq 环境变量](https://www.postgresql.org/docs/17/libpq-envars.html) 与 [PostgreSQL TLS](https://www.postgresql.org/docs/17/libpq-ssl.html) 官方文档。
