# BusyBox 下游安全回补

此目录为共享构建输入的唯一维护入口。`frontend/security/busybox-backport/` 与 `business-backend/security/busybox-backport/` 由 `python tools/sync_busybox_inputs.py` 生成；修改此处后重新生成，不手工修改副本。

基于原 Alpine `busybox 1.37.0-r31` 的精确 aports 提交，先校验原始配方、所有补丁和上游源码，再应用 Radoslav Kolev 在 BusyBox 邮件列表公开的 CVE-2025-60876 补丁。保留原配方测试，构建明确标注的 `1.37.0_p20261110-r0` 下游 APK；没有把未修改的版本号改大冒充修复。

只安装原运行镜像已有的 `busybox`、`busybox-binsh`、`ssl_client` 三个包。构建签名私钥留在临时构建阶段，运行镜像不保留该私钥，也不扩大运行时 APK 信任目录。镜像记录修改后的 APKBUILD、源码锁、补丁和包/实际文件摘要，原始漏洞扫描仍保留。包来源与补丁覆盖、实际回归及最终摘要需要单独验收，未完成前不声明归零。

APK 所需的 RFC822 构建身份使用 `Trip Planner downstream build <build@tripplanner.invalid>`；`.invalid` 是保留域名，此处为构建标识，不是支持或漏洞报告邮箱。

原 BusyBox 和 Alpine 配方/补丁按各自原条款分发，BusyBox 为 GPL-2.0-only；项目 MIT 不重新许可这些内容。对应原始源码、完整配方和修改须随实际镜像收集到发布材料。
