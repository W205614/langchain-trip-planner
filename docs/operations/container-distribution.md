# 应用镜像与源码材料

三个应用镜像的命名空间为 `ghcr.io/w205614/langchain-trip-planner-{agent,backend,frontend}`，支持 `linux/amd64`。正式版本使用 `vX.Y.Z` 标签，部署时以 Release 的 `container-manifest.json` 和实际 registry 摘要核对身份；不使用可变 `latest`。PostgreSQL、Prometheus、Alertmanager 和告警接收器按本仓配方在本地构建／运行；它们不是这三个 GHCR 应用包。当前v0.1.6升级三个应用及Go工具链配套镜像，告警接收器保持原已验收镜像。范围见[当前版本证据](../evidence/release-v0.1.6-20261112/README.md)。

## 首次使用镜像

获取对应 Tag 的源码，按 [README](../../README.md) 准备模型、高德服务端配置并首次生成 `deploy/runtime/`。应用镜像不包含配置、账号、数据库、上传或向量索引。浏览器地图 Key 通过项目根目录 `.env` 提供：

```dotenv
TRIP_VERSION=v0.1.6
AMAP_WEB_JS_KEY=替换为自己的高德Web端JS_API_Key
```

该 Key 会公开给浏览器；模型、Embedding、服务端高德 Key 不得填入这个变量。`frontend/.env` 仍供本地 Vite 开发或自行构建使用，预制镜像通过启动时的 `runtime-config.js` 接收浏览器 Key，无需重新构建。

```powershell
$imageCompose = @('-f', 'docker-compose.yml', '-f', 'docker-compose.images.yml')
docker compose @imageCompose config --quiet
docker compose @imageCompose build postgres
docker compose @imageCompose pull backend agent frontend
docker compose @imageCompose up -d --no-build --wait
```

这组命令面向全新四服务安装：首次必须执行 `build postgres`，仅拉取三个应用不足以准备新机器的数据库镜像。`up --no-build` 保证应用使用已发布制品；不要改成 `up --build` 后声称仍是公开候选。已有七服务环境沿用原 Compose、挂载和私有配置，仅将三个应用服务绑定到验收后的 registry 摘要，并为前端传入浏览器 Key；不要用首次配置脚本重置既有密钥，不使用 `down -v`。

## 许可、来源和替换

项目原创代码采用 MIT。每个应用镜像附带 `/licenses/`：项目许可、第三方通知、Python 包安装时提供的许可材料、Java JAR 内的 LICENSE/NOTICE，以及前端生产依赖的原始通知。原有系统版权文件及 Python 安装目录中的许可不删除。镜像标签中的 `org.opencontainers.image.revision` 记录发布来源，`io.tripplanner.application.revision` 记录被推广的应用层原来源；业务源码未变的组件可以复用已验收层，不能把复用解释成重新编译。

每个版本的 Release 同时提供 `container-sources-vX.Y.Z.zip`、`container-manifest.json` 和 `SHA256SUMS`。源码包按实际镜像盘点，包含 Debian/Ubuntu 源包、Alpine 精确构建提交的 APKBUILD 与校验后的源文件、Python 及 Java 运行时来源、Python 源码及不可变官方来源链接、Maven 源码 JAR、锁定的 npm 源包，以及本项目原生回补的补丁、版权和重建脚本。ONNX Runtime 的 MIT 核心提供固定提交入口与完整上游通知，其捆绑的 MPL Eigen 源码按该提交配方的校验值单独提供。源包内部另有文件摘要清单。下载后先核对外层 SHA-256，再核对内部清单。

glibc、libstdc++ 和 util-linux 的项目修改见源码包 `project/backend/security/native-backports/`，原始源包在 `debian/`。按其中脚本准备构建依赖、将对应源包放入 `/build` 并解包，再按脚本应用补丁与构建；基础镜像和版本锁保留在对应 Tag。Alpine 使用附带的 APKBUILD 和官方 `abuild fetch verify` 流程。Maven、Python 和 npm 源文件保留各自原始许可，不被项目 MIT 重新授权。

使用者可以修改、重新构建和替换共享 LGPL 组件；本项目不增加阻止为这些修改调试的限制。这里说明当前分发材料和获取入口，不把 SBOM 或漏洞扫描视为许可授权，也不对所有第三方内容作法律保证。

个别 npm 上游归档只在原始 `package.json` 声明 MIT，未附单独许可文件；材料保留其原始作者和发布声明，并随附标准 MIT 许可正文，不补造版权主体或年份。Java 依赖映射来自匹配源码的实际 CI SBOM，再按候选中 120 个 JAR 的实际字节核对。OR-Tools 捆绑库按该版本上游构建配方提供源仓库提交和补丁；这些材料不声称逐字节重现上游 wheel。

22 个 Java 依赖 JAR 未附独立许可文件，镜像另行随附校验过的上游 POM、原始源码文件头、同一 CI 解析的许可声明及固定 SPDX 数据版本中的许可全文；原二进制和完整源码不改写。Logback 的 EPL/LGPL 双许可、LatencyUtils 的 CC0 和 reactive-streams 的 MIT-0 保留各自声明，不统一替换为项目 MIT。

## 维护发布

1. 从最终干净源码构建候选，运行必要的业务、浏览器和安全检查。先以 `docker image save --output <审计归档> <Agent镜像> <Java镜像> <前端镜像>` 导出三个未封装的实际输入；通过 `tools/container_sources.py --image-archive <审计归档> ...` 盘点实际依赖并收集来源。工具核对归档配置、全部有序层和解压层摘要，并收集各历史层 APK 或 dpkg 数据库所指向的来源；最终层中被替换或移除的组件也在分发材料范围内。该归档产生于许可封装之前，不依赖下一步 seal 的输出。随后运行 `tools/container_license_supplements.py --sources <收集目录> --bundle <输出目录>/container-sources-vX.Y.Z.zip`，补齐三个 wheel 的原始通知及原生 Rust 源码锁对应的来源和许可。收集步骤不传 `--version`，由补齐步骤生成最终 ZIP 和完整摘要清单，避免将初步归档当作最终附件。补齐工具随源码和分发材料提供；下载不完整、摘要不符或来源缺失时停止发布。
2. 使用 `tools/container_release.py --version vX.Y.Z seal ...` 添加许可层并导出候选。该步骤核对源材料对应的应用层；对最终候选补验实际启动和流程，记录摘要。
3. 最终提交的 CI 和 Security gates 全部通过后创建新的 Tag 和草稿 Release，将候选镜像归档、源码材料、manifest 和校验文件上传到草稿。核对草稿后发布，使对应源码先于 GHCR 镜像公开可取。
4. 手动运行 `Publish validated application images`，填写该版本。工作流核对同一 SHA 的两条检查、Tag、正式 Release 及附件摘要，加载和推广同一镜像，不重新构建、不覆盖已有版本、不部署生产。下载公开附件只需 `contents: read`，工作流不获得仓库内容写权限。
5. 按公开发布范围设置新建 GHCR 包可见性，匿名拉取并核对 registry 摘要和许可入口；完成实际使用者验收后更新本机日常环境。

Packages 列表读取、指定版本读取、推送及公开可见性分别验收。403 或列表为空不能代替这些结果。发布后的事实和限制写入该批次交付记录，历史失败日志保留。

## v0.1.6 的不可变身份

[Release](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.6)源码为`2648d33576c987294facc7b797bbcea74af2214e`，Linux/amd64、PUBLIC。三个应用的实际Registry/config摘要如下，标签`:v0.1.6`；部署使用仓库加`@`和完整digest。四附件、真实联网验收及备份/停止边界见[本版证据](../evidence/release-v0.1.6-20261112/README.md)。

| 应用 | Registry digest | Config digest |
| --- | --- | --- |
| `agent` | `sha256:1667ee0245448902cab188ee64d82cd4ecd35acec8a078bcf4436fa581872907` | `sha256:a9727a4dbcaef100b8e56f8d95f7e41d38972d79e9ed8710039969ff4eb98a93` |
| `backend` | `sha256:666a01c19ef4f65eaf648ba8136e5f2e7a6ab0c92f3b8f4b82d69b19ff56845b` | `sha256:56e9b5099f354498870774e6d6a6f686e9fa5ee0e3adbb5a369b7cae081b47dc` |
| `frontend` | `sha256:2bbbaf799d47606226504bd4dff327811a98c19b83ad07e4da23e4e2029c482e` | `sha256:d6b6f8c16c2c59485d2cbaf13aa9d9dd52c6c22cdf54d21cedbaa96d8b3b738e` |

最终候选与匿名拉取的镜像有序层、配置及来源一致。来源ZIP6712成员与2743下载记录全部校验。历史v0.1.5及更早版本保留；下面是各版本当时的身份和验收边界。

## v0.1.5 的不可变身份

[Release](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.5)源码为`7cceca80eb8524b9fbef34a385796224426de81e`，Linux/amd64、PUBLIC。下面来自实际Registry推广及匿名获取，不是本地ID推算；标签`:v0.1.5`，部署可用仓库加`@`和精确digest。

| 应用 | Registry digest | Config digest |
| --- | --- | --- |
| `agent` | `sha256:c85d5a2d3922d7635f138da741ead33c839471568cecd7ca87cdd5b359c5dbac` | `sha256:15d0ab81bb1cc01cbdb4c2e157814c3c90493a66e1defd8ae3bf0fceaaf83087` |
| `backend` | `sha256:e07bf6b87db08f28608d5fd71db8edba53dc6cea44b8345e7a11eaa25f885cc0` | `sha256:3c61c0ea12606e3d9e711752d662d23e562a18f54af0f66ccbc864bdaeb11609` |
| `frontend` | `sha256:7911df66b5393c649138f6d232f939d22a65b18697cf75b289f8913aed8fcdd7` | `sha256:66422c85ed2e0d740776dcce9692992a273400cd206178d2c8a6e672524233b5` |

来源、四附件摘要、实际全级扫描、真实生成与最终回放边界见[本版证据](../evidence/release-v0.1.5-20261112/README.md)。对应源码ZIP6712文件，2743来源记录及128个项目文件核对；OpenSSL回补在`project/backend/security/openssl-backport/`，前端的实际安全运行层复用配方和证明在`runtime-provenance/frontend/duration-overlay/`。原生回补和第三方源码由各自脚本构建，不承诺不同时间重建逐字节相同。

本版干净源码CI实际构建全部核心服务；本机首次配置/拒绝覆盖、两条Compose配置及历史输入复制检查另行实跑。以下v0.1.4章节是保留的历史身份与当时失败，不用于冒充本版新验收。

## v0.1.4 的不可变身份

[Release](https://github.com/W205614/langchain-trip-planner/releases/tag/v0.1.4) 的源码 Tag 解引用为 `272c64b016115e357377b107daefd9bd934a361a`。下表来自实际推广回执和后续匿名拉取，平台均为 `linux/amd64`、可见性均为 public。地址中的标签为 `:v0.1.4`；已有部署可使用相应 `@sha256:...` 固定内容。

| 应用仓库 | Registry digest | Config digest |
| --- | --- | --- |
| `ghcr.io/w205614/langchain-trip-planner-agent` | `sha256:5785bdc6d48633638066799b1653eeb6bb827b70daa206e118c1e21bd945ea34` | `sha256:23cc0bafb8bb0d0dd3e19fd3f3afac33065982b83bafec8acb8056b7c9535f5c` |
| `ghcr.io/w205614/langchain-trip-planner-backend` | `sha256:65058254ba29c7f67124725208645269a9d8d4d4168d5ba1ae9247ad5f384693` | `sha256:9ab0b4a35d028596f7dfda5af8c9a0333dee29d292929b54a30cadb158dfa38f` |
| `ghcr.io/w205614/langchain-trip-planner-frontend` | `sha256:16b10ea4b4be3c32e89a8df3c950346b712b868818377500eaaa244c7d91e24d` | `sha256:5a887b9b5f0241d820098f1dee3a4f592d4c3a27f0939064de1461bbf95bb110` |

`container-manifest.json` 另存有序解压层及运行配置。Docker 本地 `inspect.Id` 不自动等于 OCI config digest；本版三项 registry digest 的实际值由 Registry 查询与匿名拉取取得，而非从本地ID推断。

Agent 原应用源码为 `e81765b257a2b88b1d463a02c6263e754d5cce00`，Java 为 `881817b01cf778da6b546197d85af0534922428f`，前端为 `843d242ff3ac5e4fe18ef6dbf3b2c6d3b51a59ce`。Agent 本机发行候选保留已验收原生运行层，仅覆盖五个已审 Python 模块；实际配方、源字节与独立运行层证明在对应源码包的运行来源材料中，不宣称它是一次新的原生冷编译。

| 正式附件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `application-images-v0.1.4.tar.gz` | 421535377 | `be69d2a7f204da40f53c24cb1d61716594e0b1e4c7be6dbf628f4b99a621d3f9` |
| `container-sources-v0.1.4.zip` | 1392212211 | `6434f9d7129766e23d8480b0af8a225b699799c926eb6396936e8bbaa72fa8c5` |
| `container-manifest.json` | 15633 | `b194f9451cb2d184e157e8b9cfb750a33b85cec715932f8d7469504e60023517` |
| `SHA256SUMS` | 284 | `fb5596cbc8b1b33e9b69c80fdacbcf75690a31ab816afe6f92a920b82f9f3466` |

源码包6678成员的CRC、原始内部摘要及2743来源下载记录均核对；111个项目重建文件中102原始字节等同Tag、9个UTF8无NUL文本仅CRLF/LF不同。外层及内部SHA始终按原始字节核对；文本等价比较不会改写附件或降低二进制检查。重建应用以公开Tag的规范源码及该包内精确第三方材料为输入。

## 源码构建与历史输入

普通源码 Docker 路线见 [README](../../README.md)。当前7cceca8的干净CI已实际构建；本机同源新安装命令实跑配置生成、拒绝覆盖及两条Compose配置检查，配置检查不称作本地冷构建。历史272构建结果在v0.1.4记录内保留。

完整Git历史用于安全源绑定工具；配置生成器本身不需要Git。`tools/restore_historical_build_inputs.py` 默认只读验证12个已公开冻结的历史原件及现有目标，不会复制缺失目标。安全CI在真实Actions环境使用其CI专用开关；本地不要伪造 `GITHUB_ACTIONS`，也不存在 `--restore-local` 参数。

需要忠实保留发行物中的历史评估输入时，在独立的完整源码检出中执行下面的PowerShell块。它只复制不存在的目标，已有不同内容由第一轮校验明确拒绝；最后再次校验，不重新生成或改写旧评估。这些历史文件不是本次新的真实模型结果。

```powershell
$ErrorActionPreference = 'Stop'
python tools/restore_historical_build_inputs.py
if ($LASTEXITCODE -ne 0) { throw 'Historical sources failed verification' }
$manifest = Get-Content -LiteralPath 'docs/evidence/optimization-20261026/historical-build-inputs/manifest.json' -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    if (-not (Test-Path -LiteralPath $entry.destination)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $entry.destination) -Force | Out-Null
        Copy-Item -LiteralPath $entry.source -Destination $entry.destination -ErrorAction Stop
    }
}
python tools/restore_historical_build_inputs.py
if ($LASTEXITCODE -ne 0) { throw 'Copied historical inputs failed verification' }
```

该块已在隔离的首次复制及重复执行场景实际通过。完整应用冷构建复用同272的[CI](https://github.com/W205614/langchain-trip-planner/actions/runs/42962698002)及[Security gates](https://github.com/W205614/langchain-trip-planner/actions/runs/42962697955)，不把本机缓存构建说成新冷编译，也不保证重建制品逐字节相同。

## 配套服务与已有环境

核心首次安装只有四服务。本地支持配方在 `deploy/images/`：

| 配方 | 构建上下文 | 实际边界 |
| --- | --- | --- |
| `postgres.Dockerfile` | `deploy/images` | PostgreSQL17.11；核心Compose已接线，先 `build postgres` |
| `prometheus.Dockerfile` | `deploy/images` | Prometheus3.16.0，本地监控镜像 |
| `alertmanager.Dockerfile` | `deploy/images` | Alertmanager0.34.1，本地监控镜像 |
| `alert-receiver.Dockerfile` | 仓库根目录 `.` | 独立stdlib接收器，UID1000、`/inbox/alerts.jsonl`、9088 |

例如前三者使用 `docker build --platform linux/amd64 --build-arg VCS_REF=<实际源码SHA> -f deploy/images/<配方> -t <本地名称> deploy/images`；receiver的context必须是仓库根。可选Caddy也有本地配方，不是第四个公开应用包，更不代表公网TLS已验收。

Tag272的生产模板receiver仍使用完整Agent配方和旧命令／挂载，并未自动切换到独立stdlib配方。不能只换image就认为兼容；若选用独立配方，须同时核对入口、UID、inbox路径及挂载，并独立验收。本轮既有七服务沿用自己的原编排，实际完成了这组契约检查；不把该本机结果扩为生产模板或任意新环境验收。

现有七服务不运行首次生成器、不替换根目录四服务模板、不删卷。升级前保留业务库、上传、私有配置和监控/inbox恢复资料；使用已验收的同一不可变应用摘要。`start`／`restart`不会加载新env，配置确需变化时只重建受影响服务。恢复与原启停状态见[单机运行手册](single-host.md)。

## 单独复核已发布版本

[Anonymous published container consumer](../../.github/workflows/consume-published-containers.yml) 是只读手动工作流：在main选择它并提供既有版本（例如v0.1.4）。它检出该次工作流的精确工具SHA，另从版本Tag解引用应用源码；匿名下载四附件、核原始SHA/CRC和源码、拉取三个不可变镜像并做断网运行探测。没有包写权限、没有重新推送或部署步骤。

本版第一次组合推广工作流的publisher已成功，但旧consumer按原始字节拒绝上述9项文本行尾，故整个[旧run42978056488](https://github.com/W205614/langchain-trip-planner/actions/runs/42978056488)不是成功状态，失败保持。修正工具ab15后，[独立run42979536910](https://github.com/W205614/langchain-trip-planner/actions/runs/42979536910)实际成功。不要重跑已有版本的publisher来验证；它本来就拒绝覆盖已存在的公开版本。

匿名获取与三项断网命令不等于整栈通过。v0.1.4另完成实际本机匿名拉取、隔离数据库恢复、四服务与浏览器检查，以及约定日常七服务升级；模型／高德正向验收仍未通过，按明确授权采用离线夹具，具体见[版本证据](../evidence/release-v0.1.4-20261111/README.md)。后续工具和文档提交不移动Tag、不修改这些附件或镜像。
