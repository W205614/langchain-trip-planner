# 原文件存储与迁移

原文件支持本地目录和 S3 兼容对象存储。业务数据库保存每个文件的实际位置；修改默认存储不会移动既有文件。下载、资料解析和删除均按记录中的位置读取，不向浏览器发放 Bucket 凭据或公开 URL。

## 配置

在后端私有环境文件配置以下字段，保持 Bucket 私有；不要把密钥写入命令行、前端或 Git。

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

1. 保留原本地目录挂载和 `.dataset-id`，先停写并准备成套备份；本机备份当前停用，本文不会自动重新启用。
2. 为新 S3 前缀配置同一个 `DATASET_ID`。在维护窗口使用新镜像运行 `docker compose run --rm --no-deps backend storage enroll`。命令不启动 Spring 或数据库，只登记空前缀；非空、未登记或其他数据集的前缀会拒绝。
3. 按正常升级方式启动服务。Flyway 之前会检查数据库、本地旧卷及 S3 标记，错误身份不会推进迁移。
4. 管理员读取 `GET /api/knowledge/admin/storage/migration?limit=25` 查看候选；使用已有登录、MFA 和 CSRF 凭证调用 `POST /api/knowledge/admin/storage/migration/{document_id}`，逐份复制并校验后更新引用。重复调用从已有目标继续核对。
5. 删除或修改与迁移冲突时，保留源文件和副本并返回 409。旧文件不自动删除；业务删除和账号注销会连同迁移保留副本一并清理。

上传先持久化配额预留，再在数据库事务外写对象并校验，最后提交元数据。网络响应丢失或校验失败会保留文件和配额，返回 `UPLOAD_RECONCILIATION_REQUIRED`。管理员通过现有上传核对接口重查；新写入一旦开始，不会仅凭一次未找到就释放不确定预留。

## 快照与隔离恢复

现有成套备份会在写入者停止后查询所有 S3 引用，并使用同一后端镜像执行对象快照。V16 之前的数据库仍使用原本地归档流程，不依赖新 CLI。

独立 CLI 为：

```text
java -jar app.jar storage export --refs /snapshot/object-refs.json --directory /snapshot/data
java -jar app.jar storage verify --directory /snapshot/data
java -jar app.jar storage restore --directory /snapshot/data --mapping-output /restore/object-location-map.json
```

`object-refs.json` 是 JSON 数组，字段为 `storage_backend`、`storage_bucket`、`storage_key`、`storage_origin`、`sha256`、`media_type`。快照内使用编号文件，记录实际大小和 SHA-256；目标文件已存在时只校验，不覆盖。活跃资料原件属于必须存在的引用；未完成上传和迁移保留副本为可选引用，缺失会明确记录为 `missing_optional_references`，不会伪造已备份字节。恢复会把所有位置（包括可选缺失引用）映射到目标前缀和新的来源指纹，避免克隆后清理误触原 S3。

包含 S3 快照的成套恢复必须传 `restore_java_backup.py --object-config <私有目标.env>`。目标文件只接受上表存储配置，并要求显式 `UPLOAD_STORAGE_BACKEND=s3`、Bucket、新前缀和目标凭据。恢复拒绝原 Bucket+Key，先验证字节，再仅对隔离 PostgreSQL 更新位置引用。快照本身只读，位置映射写入新的恢复目录。

外部 S3 恢复使用有出站能力的新隔离网络，不发布数据库端口；本地旧恢复路径仍使用 internal 网络。真实云端连通性、权限和故障域必须另行验证；S3Mock 测试不代表已经完成异机灾备。

每次恢复使用全新具名 PostgreSQL 数据卷，名称与创建结果写入恢复报告；停止恢复容器后仍保留克隆数据。工具不会自动删除恢复容器、数据卷或网络，后续清理需单独确定这些精确资源。

对象版本控制、Bucket 生命周期和备份保留是运营配置。应用删除当前对象并清理自己记录的迁移副本；云端历史版本与到期备份须依照配置生命周期处理，不把当前对象不可见等同于所有历史副本已物理销毁。

## 验证

隔离 S3Mock 使用 `adobe/s3mock:5.2.3`。设置 `TEST_S3_ENDPOINT=http://127.0.0.1:19190` 后运行 `S3ObjectStorageTest`，实际经过 SDK 的 PUT/HEAD/GET/DELETE、条件写和数据集标记；测试凭据为 `test/test`，仅测试有效。`ObjectStorageServiceTest` 和 `ObjectStorageToolTest` 验证本地兼容、流式校验、快照恢复和拒绝原位置。

S3Mock 5.x 已移除旧的 `initialBuckets`、`root` 和 `retainFilesOnExit` 配置名。验证模板使用 `COM_ADOBE_TESTING_S3MOCK_STORE_INITIAL_BUCKETS`、`COM_ADOBE_TESTING_S3MOCK_STORE_ROOT` 和 `COM_ADOBE_TESTING_S3MOCK_STORE_RETAIN_FILES_ON_EXIT`，并通过 `/favicon.ico` 等待就绪；参考[官方配置](https://github.com/adobe/S3Mock#configuration)。保留开关用于测试数据留存，不等同于生产对象存储持久性保证。
