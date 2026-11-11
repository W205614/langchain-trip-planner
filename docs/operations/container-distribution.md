# 应用镜像与源码材料

三个应用镜像的命名空间为 `ghcr.io/w205614/langchain-trip-planner-{agent,backend,frontend}`，支持 `linux/amd64`。正式版本使用 `vX.Y.Z` 标签，部署时以 Release 的 `container-manifest.json` 和实际 registry 摘要核对身份；不使用可变 `latest`。PostgreSQL、Prometheus、Alertmanager 等配套组件仍沿用部署配置，不在本轮重新分发。

## 首次使用镜像

获取对应 Tag 的源码，按 [README](../../README.md) 准备模型、高德服务端配置并首次生成 `deploy/runtime/`。应用镜像不包含配置、账号、数据库、上传或向量索引。浏览器地图 Key 通过项目根目录 `.env` 提供：

```dotenv
TRIP_VERSION=v0.1.4
AMAP_WEB_JS_KEY=替换为自己的高德Web端JS_API_Key
```

该 Key 会公开给浏览器；模型、Embedding、服务端高德 Key 不得填入这个变量。`frontend/.env` 仍供本地 Vite 开发或自行构建使用，预制镜像通过启动时的 `runtime-config.js` 接收浏览器 Key，无需重新构建。

```powershell
$imageCompose = @('-f', 'docker-compose.yml', '-f', 'docker-compose.images.yml')
docker compose @imageCompose pull backend agent frontend
docker compose @imageCompose up -d --no-build --wait
```

这组命令面向已按 README 准备好配套 PostgreSQL 镜像的首次四服务安装。已有七服务环境沿用原 Compose、挂载和私有配置，仅将三个应用服务绑定到验收后的 registry 摘要，并为前端传入浏览器 Key；不要用首次配置脚本重置既有密钥，不使用 `down -v`。

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
