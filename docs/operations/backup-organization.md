# 本机备份归档与清理

本文记录 2026-10-06 的归档与核对过程，相关恢复材料已于 2026-10-29 退役。当前备份状态见[迁移手册](java-migration.md#备份和恢复)，新目标的备份与恢复命令见[公开部署手册](public-deployment.md#备份与恢复)。下列旧路径和清理命令供历史查阅。

2026-10-06 完成备份盘点、历史归档，并按用户要求将原 `E:\project\trip-planner-backups` 整体迁移到项目内的 `E:\project\langchain-trip-planner\backups`。迁移前后 1017 个文件的 SHA-256 一致，外部旧目录已不存在。项目 `.gitignore` 中的 `/backups/` 忽略全部备份、详细文件索引和清理脚本，这些内容只留在本机，不提交 Git。

归档当时已生成并校验。目录迁移与内部清理是两个独立步骤，旧清理记录仅供查阅。

## 保留与合并

| 内容 | 当时的存放方式 |
|---|---|
| 最近已有备份 `20260923-221010` | 当时留在原目录，供 `restore_java_backup.py` 使用 |
| 2026-09-10 至 2026-09-19 的迁移前后快照、Chroma 验证数据、验收报告、旧源码 | 合并到 `history-20260910-20260919.zip`，保留原相对路径和成套快照 |
| 两个 `post-java-restored-20260915-*` 展开副本 | 每个 183 文件均与原 tar.gz 相同，清理时删除重复数据；唯一恢复报告已归档 |
| `tools/google-java-format-1.24.0-all-deps.jar` | 一次性格式化工具，无运行依赖，清理时删除；审计脚本已归档 |

原目录为 1013 文件、287.47 MiB；历史 ZIP 包含 632 文件、14.90 MiB。两个展开副本合计 366 个重复文件、107.71 MiB。Chroma 验证副本与旧快照有 3 文件不同，完整归档保留。独有的数据库、配置、上传、索引及验收材料没有按日期删除，也没有混成一套新恢复点。

## 本地清理与核对

以下是 2026-10-06 使用的核对与清理命令。旧脚本已随相关材料退役，命令仅供查阅。

```powershell
& 'E:\project\langchain-trip-planner\backups\cleanup-verified-backups.ps1' -VerifyOnly
& 'E:\project\langchain-trip-planner\backups\cleanup-verified-backups.ps1' -WhatIf
& 'E:\project\langchain-trip-planner\backups\cleanup-verified-backups.ps1'
```

脚本先检查固定归档 SHA-256 和原文件哈希，拒绝新增未盘点文件、变更文件及链接，再仅清理明确列出的直接子路径。当时保留了最近完整备份、ZIP、文件索引、说明和脚本。脚本在完成全部清理后才写入完成记录。

本次校验了所有原备份 manifest（兼容 `sha256` / `files` 字段）、归档 CRC、632 个归档文件 SHA-256，以及重复副本在归档内 tar.gz 中的对应字节。验证范围为文件一致性，数据库恢复和业务读回另需恢复演练；本次保留运行数据及 Docker 配置。

## 读取历史恢复点

历史记录中的外部路径、项目内同名目录和归档名均用于追溯当时的文件。归档与恢复材料退役后，日常恢复应使用实际可访问且已经验证的独立备份。当前入口见[备份与恢复](java-migration.md#备份和恢复)。旧 Python 迁移数据只供历史核对。

当时的归档通过 `post-java-20260915-1331/agent-and-uploads.tar.gz` 保存了两个恢复副本的共同数据，并保留 `post-java-restored-20260915-1333/restore-report.json` 的原始路径。

当前 `backup_java_deployment.py` 的 Java/Flyway-v2 格式允许输出到仓库外或已忽略的 `backups/` 下全新目录，不覆盖已有恢复点。它从实际容器识别绑定目录／命名卷，成套保存数据库、上传、Chroma、运行账本及有效私有配置；可加 `--archive-images` 归档旧镜像。旧 Java/Flyway 格式仍兼容读取。恢复输出始终须放在新的仓库外目录；可加 `--candidate-image` 在恢复副本验证新迁移，详见[最终规划运维](final-planning.md)。每个新恢复点单独执行恢复演练。

正式回切前先备份当前数据，并核对所需镜像、网络和恢复环境。数据库、运行配置、账号记录和未脱敏日志留在私有目录，GitHub 保存操作说明与脱敏结果。
