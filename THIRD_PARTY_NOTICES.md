# 第三方许可与分发说明

项目原创代码与已获授权的项目截图使用根目录 [MIT License](LICENSE)，版权人为 `Copyright (c) 2026 W205614`。第三方代码、补丁、数据、商标和服务分别遵循各自条款，MIT 不替代其许可证。本次授权适用于包含该 LICENSE 的版本，不追溯授予此前提交或标签新的许可。

## 仓库直接保存的上游材料

| 路径或材料 | 来源与适用许可 |
|---|---|
| `contracts/openapi-3.1.schema.json` | OpenAPI Initiative 的 [2022-10-07 schema](https://spec.openapis.org/oas/3.1/schema/2022-10-07)，Copyright The Linux Foundation；原样保留，[Apache-2.0 全文](contracts/openapi-3.1.LICENSE)及校验说明见 [contracts/README.md](contracts/README.md) |
| `backend/security/native-backports/patches/glibc-19499-sourceware.patch`、`glibc-84243-trixie.patch` | GNU C Library，上游作者与 Free Software Foundation 版权声明保留；涉及的库代码和回归测试使用 LGPL-2.1-or-later。后者为本项目对上游补丁的适配，差异和日期见补丁头 |
| `backend/security/native-backports/patches/glibc-5435-trixie.patch` | GNU C Library 的 `resolv/ns_print.c`；该文件使用 ISC 许可，版权归 Internet Systems Consortium / Internet Software Consortium，不能概括为 LGPL |
| `backend/security/native-backports/patches/gcc-*.patch` | GCC / libstdc++，保留上游补丁作者和来源；相关代码使用 GPL-3.0-or-later，运行库文件另附 GCC Runtime Library Exception 3.1，回归测试保留上游许可 |
| `backend/security/native-backports/patches/util-*.patch` 中的 `libmount/src/*` | util-linux / libmount，LGPL-2.1-or-later；保留 Karel Zak、Christian Brauner 等原作者声明 |
| 上述 util-linux 补丁中的 `sys-utils/nsenter.c` | GPL-2.0-only；保留 Eric Biederman 版权声明 |
| 上述 util-linux 补丁中的 `sys-utils/unshare.c`、`sys-utils/mount.8.adoc` | GPL-2.0-or-later；保留原作者及手册版权声明 |
| 上述 util-linux 补丁中的 `include/fileutils.h`、`lib/fileutils.c` | 上游明确声明为 public domain，原声明保留 |
| 上述 util-linux 补丁中的 `term-utils/wall.c`、`term-utils/write.c` | 保留 University of California, Berkeley 的完整 BSD 版权、条件及免责条款 |

上游版权与逐文件声明保存在 [NATIVE-NOTICES.txt](docs/licenses/NATIVE-NOTICES.txt)，包括 ISC、public domain 和 Berkeley BSD 原文。其他适用许可全文：[LGPL-2.1](docs/licenses/LGPL-2.1.txt)、[GPL-2.0](docs/licenses/GPL-2.0.txt)、[GPL-3.0](docs/licenses/GPL-3.0.txt)、[GCC Runtime Library Exception 3.1](docs/licenses/GCC-exception-3.1.txt)。补丁的来源提交、变更说明和固定摘要见 [native-backports/README.md](backend/security/native-backports/README.md)、`patches.sha256`、`sources.sha256` 及构建脚本；添加通知不改变补丁字节。

This product includes software developed by the University of California, Berkeley and its contributors.

## 安装依赖与 PDF 渲染

Python、Java 和前端依赖由各自的依赖管理器安装，版本依据 `backend/requirements.lock`、`business-backend/pom.xml` 和 `frontend/package-lock.json`。它们不因项目采用 MIT 而改变许可。运行依赖中包括 MPL 等非 MIT 许可，分发时须保留所用版本随包提供的许可证和通知；不能只复制本项目 LICENSE。

容器分发另按实际候选核对其系统包和 wheel/JAR/浏览器依赖；包括 OR-Tools 内的 Coin-OR/Eigen 等捆绑库、NumPy 的上游通知和 GCC Runtime Library Exception，以及 cryptography 实际链接的 OpenSSL。个别 npm 包只提供原始 MIT 声明时，保留发布元数据并附标准正文，不把项目自己的版权行移用于第三方。具体来源、源码和通知由对应 Release 的材料清单列出。

当前 PDF 渲染使用 [pypdfium2 5.15.0](https://pypi.org/project/pypdfium2/5.15.0/)。绑定提供 Apache-2.0 / BSD-3-Clause 许可选项，PDFium 及其捆绑组件适用各自条款，详见 [pypdfium2 上游许可说明](https://pypdfium2-team.github.io/pypdfium2/readme.html#licensing)。安装时保留官方 wheel 内的许可证和第三方声明；再次分发 wheel、容器或其他二进制前，应核对实际安装包的许可文件，不能把所有捆绑组件统称为 BSD 或 MIT。

此前使用 PyMuPDF 的历史版本仍需按 [Artifex 的 AGPL / 商业双许可](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)单独核对，不因当前替换依赖而自动改变历史版本的许可或免除既有义务。

## 源码发布与镜像

`v0.1.0` 至 `v0.1.2` 的 GitHub Release 只发布项目源码。`v0.1.3` 新增三个应用容器的分发流程；实际发布结果以对应 Release 的制品与校验清单为准，获取和许可材料见[应用镜像与源码材料](docs/operations/container-distribution.md)。Dockerfile 下载的第三方源代码和基底镜像仍受各自许可约束；本项目的构建脚本不授予额外分发权。

Prometheus 和 Alertmanager 的镜像构建保留各自上游 `LICENSE`、`NOTICE`，Caddy 沿用官方运行镜像。Agent 中回补的 glibc、libstdc++ 和 util-linux 也保留发行版版权文件。公开分发镜像或其他二进制前，须针对实际产物准备所需的许可、通知、对应源码及修改与重建材料，并完成该产物的核验；来源缺失的制品不发布。本文件记录已检查的来源和适用边界，不代表对所有第三方材料的法律保证。

高德地图、景点图片和模型 API 的使用还受供应商服务条款约束，项目 MIT 不授予地图或景点图片的再分发许可。演示知识库中的价格、线路为示例；上传者须对其上传和共享材料拥有相应权利。
