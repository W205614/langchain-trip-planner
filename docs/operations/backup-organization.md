# 本机备份归档与清理

2026-10-06 对仓库外的 `E:\project\trip-planner-backups` 完成盘点和历史归档。归档已生成并验证，原目录清理待执行；完成状态以本机 `cleanup-completed.json` 为准。备份、详细文件索引和清理脚本均留在本机，不提交 Git。

## 保留与合并

| 内容 | 存放方式 |
|---|---|
| 最近已有备份 `20260923-221010` | 保持原目录，可直接传给 `restore_java_backup.py`；不是本次新生成的备份 |
| 2026-09-10 至 2026-09-19 的迁移前后快照、Chroma 验证数据、验收报告、旧源码 | 合并到 `history-20260910-20260919.zip`，保留原相对路径和成套快照 |
| 两个 `post-java-restored-20260915-*` 展开副本 | 每个 183 文件均与原 tar.gz 相同，清理时删除重复数据；唯一恢复报告已归档 |
| `tools/google-java-format-1.24.0-all-deps.jar` | 一次性格式化工具，无运行依赖，清理时删除；审计脚本已归档 |

原目录为 1013 文件、287.47 MiB；历史 ZIP 包含 632 文件、14.90 MiB。两个展开副本合计 366 个重复文件、107.71 MiB。Chroma 验证副本与旧快照有 3 文件不同，完整归档保留。独有的数据库、配置、上传、索引及验收材料没有按日期删除，也没有混成一套新恢复点。

## 本地清理与核对

本机目录中的 `README.md` 和 `cleanup-verified-backups.ps1` 给出核对与清理入口：

```powershell
& 'E:\project\trip-planner-backups\cleanup-verified-backups.ps1' -VerifyOnly
& 'E:\project\trip-planner-backups\cleanup-verified-backups.ps1' -WhatIf
& 'E:\project\trip-planner-backups\cleanup-verified-backups.ps1'
```

脚本先检查固定归档 SHA-256 和原文件哈希，拒绝新增未盘点文件、变更文件及链接，再仅清理明确列出的直接子路径。最近完整备份、ZIP、文件索引、说明和脚本保留。未完成全部清理不会写入完成记录。

本次校验了所有原备份 manifest（兼容 `sha256` / `files` 字段）、归档 CRC、632 个归档文件 SHA-256，以及重复副本在归档内 tar.gz 中的对应字节。**文件一致性不等于恢复演练**；本次未重新恢复数据库、未生成当前运行数据的新备份，也未变更运行数据或 Docker 配置。

## 读取历史恢复点

历史记录中的本机路径是迁移当时的位置。清理后，相同目录名和源码 ZIP 可在历史归档内找到。先解压到全新的仓库外目录，选择完整快照，再按[运行手册](java-migration.md)恢复到独立数据库和文件目录。旧 Python 迁移数据只供历史核对，不能直接导入当前 Java 数据库。

两个恢复副本的重复展开数据不再独立保存；如需重建，从归档内 `post-java-20260915-1331/agent-and-uploads.tar.gz` 提取到新目录。`post-java-restored-20260915-1333/restore-report.json` 在 ZIP 内保留原路径；这是历史报告，不证明旧克隆容器或数据库现在仍存在。

历史镜像标识和配置不保证当前机器仍具备镜像或兼容网络。正式回切仍需先备份当前数据，并核对恢复环境。数据库、运行配置、账号记录和未脱敏日志含敏感信息，GitHub 仅保存这份操作说明。
