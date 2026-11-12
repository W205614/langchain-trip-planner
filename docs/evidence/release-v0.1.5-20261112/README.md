# v0.1.5：OpenSSL回补、时长修复与真实链路验收

核对日期：2026-11-12。正式[Release](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.5)，ID 442450824；源码Tag解引用`7cceca80eb8524b9fbef34a385796224426de81e`。平台Linux/amd64，三个既有PUBLIC GHCR应用仓库，配套四服务仍按本仓配方本地构建，不另建公开包。结构化摘要见[verification.json](verification.json)。

| 组件 | Registry digest | Config digest |
| --- | --- | --- |
| `agent` | `sha256:c85d5a2d3922d7635f138da741ead33c839471568cecd7ca87cdd5b359c5dbac` | `sha256:15d0ab81bb1cc01cbdb4c2e157814c3c90493a66e1defd8ae3bf0fceaaf83087` |
| `backend` | `sha256:e07bf6b87db08f28608d5fd71db8edba53dc6cea44b8345e7a11eaa25f885cc0` | `sha256:3c61c0ea12606e3d9e711752d662d23e562a18f54af0f66ccbc864bdaeb11609` |
| `frontend` | `sha256:7911df66b5393c649138f6d232f939d22a65b18697cf75b289f8913aed8fcdd7` | `sha256:66422c85ed2e0d740776dcce9692992a273400cd206178d2c8a6e672524233b5` |

| 正式附件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `application-images-v0.1.5.tar.gz` | 431902603 | `sha256:234b06b6d2f89e759046b22580f8d029bf0512ca2f0d6fc2fb4b096e7d0f07ae` |
| `container-manifest.json` | 16137 | `sha256:6a715edce8e65f1bfa0fd2358377e8653347dd98e6e4b1ddd854a8740776167b` |
| `container-sources-v0.1.5.zip` | 1392486212 | `sha256:93c5c7f7abec550f18b3a8236778ce939c6bc8c076cff7531956568b74ecd8ff` |
| `SHA256SUMS` | 284 | `sha256:f46dc31a877380b7b740b3297f4a2b1685cb9f333032c63ef09c3e88520b0c65` |

Registry digest由实际查询与匿名拉取得出，不从Docker本地ID推断。所有应用层、运行配置与已验收封装档案匹配；公开Tag、附件和镜像不覆盖。正常v0.1.0–v0.1.4历史保留，不为验证轮次再建版本。

## 修复、构建与安全

Agent在Debian3.5.7-1~deb13u3上回补五个上游OpenSSL修复，三个下游包为`3.5.7-1~deb13u3+trip1`。完整Debian包测试、补丁回归、ABI和最终受限运行时检查通过；这是本项目回补，不称为Debian已发布u4。前端根据实际时间轴计算合计，修复真实截图暴露的旧240分钟估计与实际180分钟不一致。源码收集排除字节码，Windows检出保留补丁与安全证据原字节。

Agent应用来源018b861，Java881817b，前端f4ca5c5；封装来源为本页完整发布SHA。前端在官方AlpineCDN构建故障后，采用已验收881运行层加真实f4编译HTML；全部非HTML文件与运行配置逐项保持，HTML与实际构建输出一致。源码ZIP保留精确配方、差异证明、第三方源包、必要许可、补丁和构建材料；镜像附带`/licenses`。原始失败现场保留。

- [CI43140025791](https://github.com/W205614/langchain-trip-planner/actions/runs/43140025791)：同源4/4实际成功；[Security43140025836](https://github.com/W205614/langchain-trip-planner/actions/runs/43140025836)：同源14/14实际成功，包含干净应用/配套构建与全级扫描。
- [镜像推广与匿名消费者](https://github.com/W205614/langchain-trip-planner/actions/runs/43150589956)：两个job实际成功；推广既有镜像档案，不重建。消费者无凭据完整下载四附件，核外层SHA、ZIP CRC/内部摘要、源码和三个公开镜像，断网运行探测通过。本机另作无登录精确摘要拉取、层/config比对及整栈运行。
- 最终Agent222条原始匹配：15实际OpenSSL回补、64固定升级、118缺席组件、15执行路径、6构建条件及4条独立Chroma上下文；适用未解决0、风险接受0。Java和前端原始0；receiver3条均有源码回补。原始匹配不删除，条件绑定材料见[安全证据](../native-security-20261112/)。
- Java11条GET/CSRF误报逐条核实全部44路径，按授权关闭，与实际源码修复分列。发布源远端Code/Secret open0；Dependabot禁用，不冒充clean，另有实际解析依赖及镜像门禁。

## 真实生成与最终回放

供应商额度恢复后追加授权1次模型/60次高德MCP。实际提交1次模型、34次MCP，南京一天一个景点、14:30–17:30窗口，生成、持久化、读取与刷新通过；全局账本未清零。结果仍degraded，营业、预约、端点与接驳未知保留。较早两次真实失败分别为时窗不足与额度/调用预算失败，继续保留failed。

真实生成发生在较早封装候选，最终Agent/Java提供商运行层完全相同。最终封装镜像实际恢复原始数据库，登录、同记录读取与刷新通过，摘要180分钟，新增供应商调用0，原始保存行程、8张outbound账本表和全局逻辑账本不变。不能把该回放称为最终封装上的第二次真实生成。

公开后另在独立四服务环境使用匿名拉取的三个不可变摘要及已验收PostgreSQL，实际恢复同一数据库并通过读取/刷新/浏览器验收，新增调用0，结束后四服务停止。时效自然过期时，Java如实投影为待核验；未改时钟、有效期或保存事实。[三张真实PNG和边界](../../screenshots/release-v0.1.5/README.md)来自发布前最终候选回放，公开制品身份另有严格对应证明。

## 本机日常验收与保留

既有七服务已实际升级三个公开应用摘要；四个已验收本地支持镜像保持。五个Docker健康检查、三个监控/接收器就绪及trip/trip-agent目标up通过。端口、挂载、安全配置及显式环境保持，46表行摘要和4上传文件摘要不变，45账号/61行程/3资料/Flyway20保持，最终七服务恢复停止。日常仅白名单只读HTTP，不打开日常浏览器或提交生成；outbound表亦在全表核验中。

原镜像、旧备份和本次业务/配置/监控inbox恢复点保留，自动备份任务Disabled。数据库逻辑备份及本次合成库的实际恢复不等于完整七服务克隆恢复。

README与本页随独立文档提交交付main，不把文档SHA冒充镜像来源。文档实际CI、远端Maven/Tags/Releases复核与文件回收由本轮最终回执分别记录；它们不重建、不重发本版。

RAG/视觉、浏览器地图/照片、SMTP/S3、公网TLS、异地恢复、多实例与生产容量没有本轮新增真实验收。正常历史证据保持原适用边界。
