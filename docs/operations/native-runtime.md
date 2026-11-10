# 原生运行时维护边界

本轮候选将 Agent 应用解释器迁移到 CPython 3.13.18，Java 运行层改为 Temurin 21 的 Alpine 3.24 基座。镜像、扫描和运行是否已正式交付，以对应 Release 的制品摘要及验收记录为准；本文描述构建和运行契约。

## Agent 的 glibc 硬化

Agent 使用 Debian 13 的 glibc 2.41 源码及真实重建的 `2.41-12+deb13u4+trip4` 运行包。成熟上游回补之外，三个额外下游改动分别处理 CVE-2019-1010022、CVE-2019-1010024 和 CVE-2019-1010025：

- 在系统分配的线程栈与 TLS 之间保留不可读写的保护区。
- 关闭线程栈缓存复用，逐次从内核随机源生成映射地址提示；仅接受精确命中的地址，不使用 `MAP_FIXED`。碰撞最多尝试 32 次，失败时线程创建明确失败。
- 线程 arena 在满足页对齐约束的地址网格上使用独立随机偏移。偏移在进程及其 fork 后代中保持，以正确释放既有分配。

支持范围是 **Linux x86_64 LP64**，不是跨架构补丁。要求内核支持 `getrandom`，且熵池已经初始化；容器安全策略须允许正常的 `getrandom`、`mmap`、`mprotect`、`munmap` 和线程创建。测试使用 Docker 默认 seccomp 并进一步移除 capabilities；自定义 seccomp 或其他内核环境须单独验收。

随机读取错误或短读没有可预测的后备值。线程栈分配遇到该问题会使 `pthread_create` 失败；首次 allocator 初始化遇到该问题会以 `malloc: cannot initialize arena randomization` 终止进程。不要把这种失败当作可忽略的启动警告。应检查内核、熵源和容器策略，或按升级恢复方案切回已验证的完整版本；不能仅修改包版本声明冒充修复。

关闭栈缓存增加映射和回收开销，未承诺原有性能、容量或 SLA。调用方提供的栈仍遵守其原有契约；TLS 保护区也不代表任意内存泄漏、任意越界访问或所有利用方式均被防止。随机 arena 偏移不是每个 arena 的独立随机值；显式 huge page 的对齐约束会减少可选偏移。

当前实验环境没有预留物理 huge pages。hugetlb 测试覆盖普通页及实际失败后的普通页回退；不代表成功的物理 huge-page 分配已验收。

## 其他实际运行约束

Agent 继续以非 root 用户运行，不分配 subordinate UID/GID。镜像移除了应用无调用路径的管理和辅助命令；还移除闲置的终端数据库编译解析库 `libtic` 及其唯一保留调用方 `toe`，保留实际使用的 ncurses/tinfo、其他运行库及包管理清单。具体删除对象和原文件摘要见镜像 `/usr/local/share/tripplanner/restricted-runtime-tools.json`。镜像面向应用运行，不作为包含完整系统管理工具的通用交互环境。

Java 的 musl 基座变更须覆盖数据库、会话、TLS/DNS 及业务接口的实际运行验证。BusyBox 的 wget 修复分别进入 Java 和前端镜像，不据固定健康检查 URL 将未打补丁的组件记为修复。PAM、dash 与 CPython 的回补保留各自来源、真实二进制和回归证据。

## 源码、构建与恢复

`backend/security/native-backports/` 保存成熟回补及 Debian 构建脚本，`backend/security/glibc-hardening/` 保存三项额外改动及真实回归。CPython、PAM、dash 和 BusyBox 的目录均保留独立校验清单、构建配方和上游来源。完整镜像构建通过 Dockerfile 串联源码下载校验、零 fuzz 打补丁、实际编译、ABI 对照及必要测试；不能只替换已安装包的版本字符串。

Release 的对应源码材料必须包含原始源包、本项目补丁、必要构建和测试文件及各自许可。镜像中的 GPL/LGPL 组件不由项目 MIT 重新授权，详见 [第三方说明](../../THIRD_PARTY_NOTICES.md) 和 [镜像分发约定](container-distribution.md)。

日常升级沿用已有私有 Compose、数据挂载和密钥。先建立可核验的恢复点，再绑定已经验收的镜像摘要；本次原生库变化不自行修改业务数据或向量维度。仅回切镜像不等于回滚数据库，具体恢复步骤沿用 [升级和恢复手册](java-migration.md)。
