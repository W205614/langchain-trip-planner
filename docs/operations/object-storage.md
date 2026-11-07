# 原文件存储与迁移

原文件支持本地目录和 S3 兼容对象存储。业务数据库保存每个文件的实际位置；修改默认存储不会移动既有文件。下载、资料解析和删除均按记录中的位置读取，不向浏览器发放 Bucket 凭据或公开 URL。

## 配置

在后端私有环境文件配置以下字段，保持 Bucket 私有；不要把密钥写入命令行、前端或 Git。

新安装先写入 `backend/.env` 再生成运行配置；已有安装修改实际使用的 `deploy/runtime/business.env` 或公开环境的 `deploy/runtime-production/business.env`，按下面迁移步骤处理已有数据。仅改源 `.env` 不会改变现有容器，重启也不会自动读取未传入服务的文件。

一个新安装的 S3 示例为：

```dotenv
UPLOAD_STORAGE_BACKEND=s3
S3_ENDPOINT=https://s3.example.com
S3_REGION=us-east-1
S3_BUCKET=your-existing-private-bucket
S3_PREFIX=trip/uploads
S3_PATH_STYLE=true
S3_ACCESS_KEY_ID=your-private-access-key
S3_SECRET_ACCESS_KEY=your-private-secret-key
S3_ALLOW_HTTP=false
```

AWS S3 使用该区域的正确 Region，`S3_ENDPOINT` 留空且通常 `S3_PATH_STYLE=false`；兼容服务的寻址方式按供应商配置。Bucket 必须先创建，前缀专属于该数据集。

| 配置 | 说明 |
|---|---|
| `UPLOAD_STORAGE_BACKEND` | `local`（默认）或 `s3` |
| `UPLOAD_DIR` | 本地原文件根目录，迁移后仍保留旧挂载 |
| `S3_ENDPOINT` | 官方 AWS 可留空；兼容服务填写 HTTPS 地址 |
| `S3_REGION` | 请求签名 Region，默认 `us-east-1` |
| `S3_BUCKET` | 已创建的私有 Bucket，应用不会创建 Bucket |
| `S3_PREFIX` | 专属于本数据集的前缀，默认 `uploads` |
| `S3_PATH_STYLE` | 兼容服务需要路径式寻址时设 `true` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | 成对配置，均留空时使用 AWS SDK 默认凭据链 |
| `S3_SESSION_TOKEN` | 使用临时凭据时配置 |
| `S3_ALLOW_HTTP` | 仅隔离测试允许 `true`；公网使用 TLS |

所需操作为 `GetObject`、`PutObject`、`DeleteObject` 和指定前缀的 `ListBucket`。上传采用不可变随机 Key 和条件写，读取核对使用 HEAD 与 SHA-256；兼容服务必须支持这些契约。AWS SDK Java v2 固定为 `2.57.3`，使用同步 URLConnection 客户端，不引入 Netty/Apache 客户端。

每条 S3 引用绑定规范化端点与 Region 的来源指纹，凭据轮换不改变该指纹。一个部署使用一个 S3 来源，可在同一来源内迁移 Bucket／前缀；已有 S3 数据时直接改变端点或 Region 会在 Flyway 前拒绝。更换 S3 服务商需使用显式快照恢复和位置映射，不能仅修改默认地址让旧 Key 被重新解释。未登记来源的旧 S3 元数据不会被自动认定兼容。

## 已有数据切换

1. 保留原本地目录挂载和 `.dataset-id`，先停写并准备成套备份；备份任务的当前状态与维护入口见[迁移手册](java-migration.md#备份和恢复)。
2. 为新 S3 前缀配置同一个 `DATASET_ID`。在维护窗口使用新镜像运行 `backend storage enroll`，必须带实际部署同一套 Compose 参数。公开环境示例为 `docker compose --env-file deploy/runtime-production/public.env -f docker-compose.production.yml -f docker-compose.public.yml run --rm --no-deps backend storage enroll`；云库项目继续加入 `-f docker-compose.cloud-db.yml`。仅默认四服务安装才直接使用 `docker compose run --rm --no-deps backend storage enroll`。命令不启动 Spring 或数据库，只登记空前缀；非空、未登记或其他数据集的前缀会拒绝。
3. 按正常升级方式启动服务。Flyway 之前会检查数据库、本地旧卷及 S3 标记，错误身份不会推进迁移。
4. 管理员读取 `GET /api/knowledge/admin/storage/migration?limit=25` 查看候选；使用已有登录、MFA 和 CSRF 凭证调用 `POST /api/knowledge/admin/storage/migration/{document_id}`，逐份复制并校验后更新引用。重复调用从已有目标继续核对。
5. 删除或修改与迁移冲突时，保留源文件和副本并返回 409。旧文件不自动删除；业务删除和账号注销会连同迁移保留副本一并清理。

上传先持久化配额预留，再在数据库事务外写对象并校验，最后提交元数据。网络响应丢失或校验失败会保留文件和配额，返回 `UPLOAD_RECONCILIATION_REQUIRED`。管理员通过现有上传核对接口重查；新写入一旦开始，不会仅凭一次未找到就释放不确定预留。

完成管理员登录／MFA 后，将有效 API Bearer token 放入当前进程的 `TRIP_ADMIN_TOKEN`，不要写入脚本或输出。PowerShell 可直接预览并迁移选定资料；使用 Bearer 时无需附加浏览器 CSRF，Cookie 会话仍需相应 CSRF 凭证：

```powershell
$site = 'https://trip.example.com'
$headers = @{ Authorization = "Bearer $env:TRIP_ADMIN_TOKEN" }
Invoke-RestMethod "$site/api/knowledge/admin/storage" -Headers $headers
$preview = Invoke-RestMethod "$site/api/knowledge/admin/storage/migration?limit=25" -Headers $headers
$preview.candidates
$documentId = 123 # 换成预览中确认要迁移的一份资料编号
Invoke-RestMethod "$site/api/knowledge/admin/storage/migration/$documentId" -Method Post -Headers $headers
```

成功结果为 `status=switched`、`source_retained=true`，重跑同一资料为 `already_current`；409表示复制期间业务内容改变，503表示存储未完成，修复后重试。`GET .../storage` 的 `connectivity_checked=false` 表示配置已读取；实际连接按下方上传、下载及删除流程检查。

注销清理遵循相同规则：已开始但结果仍未知的写入，在原件暂时缺失时保留预约与额度，任务保持 `waiting_for_upload`。供应商稍后发布原件后，清理会继续删除它；已提交、已释放或尚未开始写入的预约可正常清理。管理员使用 `POST /api/knowledge/admin/uploads/reconcile` 核对状态，并结合供应商请求记录处理仍未确认的结果。

## 快照与隔离恢复

现有成套备份会在写入者停止后查询所有 S3 引用，并使用同一后端镜像执行对象快照。V16 之前的数据库仍使用原本地归档流程，不依赖新 CLI。

独立 CLI 为：

```text
java -jar app.jar storage export --refs /snapshot/object-refs.json --directory /snapshot/data
java -jar app.jar storage verify --directory /snapshot/data
java -jar app.jar storage restore --directory /snapshot/data --mapping-output /restore/object-location-map.json
```

上述 Java 命令用于已经具有 `app.jar` 的应用容器。Docker 主机可直接复用同一后端镜像／环境导出，无需主机安装 Java；先按实际 Compose 参数停止 backend／agent，再执行：

```powershell
python -m pip install python-dotenv==1.2.3
python backend/scripts/storage_objects.py export --project trip-production --output E:\trip-private\object-snapshot-new
```

成套备份使用 `backup_java_deployment.py`，它还保存数据库、原本地文件、Agent 数据与私有环境。两种导出都按 backend 实际 JDBC 查询引用；云库 TLS 及客户端证书依赖见 [云库备份手册](cloud-postgresql-backup.md)。独立 SDK 容器会投影 `S3_*`／`AWS_*` 环境，显式成对凭据和可选会话令牌可直接使用；本地 AWS profile／WebIdentity 文件挂载需要另行配置，当前快照工具直接支持显式环境凭据和会话令牌。

`object-refs.json` 是 JSON 数组，字段为 `storage_backend`、`storage_bucket`、`storage_key`、`storage_origin`、`sha256`、`media_type`。快照内使用编号文件，记录实际大小和 SHA-256；目标文件已存在时只校验，不覆盖。活跃资料原件属于必须存在的引用；未完成上传和迁移保留副本为可选引用，缺失会明确记录为 `missing_optional_references`。恢复会把所有位置（包括可选缺失引用）映射到目标前缀和新的来源指纹，避免克隆后清理误触原 S3。

包含 S3 快照的成套恢复必须传 `restore_java_backup.py --object-config <私有目标.env>`。目标文件只接受上表存储配置，并要求显式 `UPLOAD_STORAGE_BACKEND=s3`、Bucket、新前缀和目标凭据。恢复拒绝原 Bucket+Key，先验证字节，再仅对隔离 PostgreSQL 更新位置引用。快照本身只读，位置映射写入新的恢复目录。

外部 S3 恢复使用有出站能力的新隔离网络，不发布数据库端口；本地旧恢复路径仍使用 internal 网络。真实云端连通性、权限和异机恢复需在选定平台联调。

每次恢复使用全新具名 PostgreSQL 数据卷，名称与创建结果写入恢复报告；停止恢复容器后仍保留克隆数据。工具不会自动删除恢复容器、数据卷或网络，后续清理需单独确定这些精确资源。

对象版本控制、Bucket 生命周期和备份保留是运营配置。应用删除当前对象并清理自己记录的迁移副本；云端历史版本与到期备份依照配置的生命周期清理。

## 验证

真实目标先做一份不含个人信息的小图片／PDF上传，确认审核前原件能被管理员下载，并比较源文件和下载文件 SHA-256；解析／发布会调用模型，需另计预算。再迁移一份已有测试资料，确认状态和下载字节一致；删除测试资料后检查删除作业及对象清理完成。最后执行成套备份和新目标恢复，核对恢复原件 SHA-256。真实云端 IAM、条件写、TLS、故障恢复及历史版本清理需在所选服务验证。

删除联调可在隔离数据库／供应商 SQL 控制台只读查询对应作业（替换测试资料编号）：

```sql
SELECT id,document_version,phase,status,last_error
FROM knowledge_ingest_jobs WHERE document_id=123 ORDER BY id DESC;
```

相应 `phase=delete`、目标版本的作业应为 `succeeded`，并在对象服务确认当前对象不存在。历史版本仍遵循 Bucket 生命周期。

隔离 S3Mock 使用 `adobe/s3mock:5.2.3`。设置 `TEST_S3_ENDPOINT=http://127.0.0.1:19190` 后运行 `S3ObjectStorageTest`，实际经过 SDK 的 PUT/HEAD/GET/DELETE、条件写和数据集标记；测试凭据为 `test/test`，仅测试有效。`ObjectStorageServiceTest` 和 `ObjectStorageToolTest` 验证本地兼容、流式校验、快照恢复和拒绝原位置。

S3Mock 5.x 已移除旧的 `initialBuckets`、`root` 和 `retainFilesOnExit` 配置名。验证模板使用 `COM_ADOBE_TESTING_S3MOCK_STORE_INITIAL_BUCKETS`、`COM_ADOBE_TESTING_S3MOCK_STORE_ROOT` 和 `COM_ADOBE_TESTING_S3MOCK_STORE_RETAIN_FILES_ON_EXIT`，并通过 `/favicon.ico` 等待就绪；参考[官方配置](https://github.com/adobe/S3Mock#configuration)。保留开关用于测试数据留存，生产环境按所选对象存储的持久化和生命周期配置运行。
