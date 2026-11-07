# 接口契约

[`public-operations.openapi.yaml`](public-operations.openapi.yaml) 是公开运营增量的 OpenAPI 3.1 契约，覆盖邮箱账号、反馈、配额、运营后台、邮件发件箱、导出、注销及对象存储迁移。既有行程接口与 `internal-v1`/`internal-v2` 另行维护。

修改相关 Controller、请求 record、返回白名单或权限时，同步更新本文件。`x-java-record` 指向实际请求类型；返回结构以服务层显式 SELECT 列表及 `AccountAccess.publicUser` 为准。Cookie 与 Bearer 认证、CSRF、管理员 MFA、反馈 `If-Match`、注销回执均在各操作中说明。契约不含密钥、令牌样例或投递正文。

在仓库根目录使用已安装后端依赖的 Python 执行：

```sh
python backend/scripts/check_public_contract.py
```

检查器核对相关 Java 路由、11 个请求 record、6 个响应白名单、配额字段、本地引用及 OpenAPI/JSON Schema 格式；CI 的 Python Agent job 执行同一命令。它不能代替真实 HTTP、SMTP、对象存储和浏览器验收。

`openapi-3.1.schema.json` 原样固定自 OpenAPI Initiative 的 [2022-10-07 OpenAPI 3.1 元 schema](https://spec.openapis.org/oas/3.1/schema/2022-10-07)，SHA-256 为 `da01ba28852cac0de53893797cb8d1942bc3b05084f526dcc216717dec314ed0`，许可证见 [`openapi-3.1.LICENSE`](openapi-3.1.LICENSE)。正式验证离线运行，另用 JSON Schema 2020-12 验证每个组件 schema。不要手改上游文件；升级时从官方地址重新获取，核对版本及许可证，再更新检查器中的哈希。
