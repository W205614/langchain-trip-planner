# 运行治理、既有数据升级与独立回退演练

本手册描述代码能力与执行边界。最终部署、真实恢复耗时、角色体验和 CI 结果以 `docs/evidence/optimization-20261026/` 的对应记录为准。源码与纯故障测试不表示本机日常服务已升级，也不表示已经完成异机灾备。

## 既有数据演练：先计划，再由主任务串行执行

`existing_data_rehearsal.py` 只接受 `Java/Flyway-v2`。它拒绝旧 V1 恢复路径，避免在日常 PostgreSQL 内创建数据库。默认模式只校验备份文件并输出计划，不创建容器或恢复目录。

本轮只读盘点确认，`backups/current-20261025-improvement` 是一份 2026-10-25 的既有数据快照，包含原表列元数据。它适合候选兼容性演练，但不能代替正式日常升级前的新备份，也不能证明快照之后的写入已获保护。

```powershell
python backend/scripts/existing_data_rehearsal.py `
  --backup E:\project\langchain-trip-planner\backups\current-20261025-improvement `
  --output E:\recovery\trip-opt-20261026-existing-data `
  --report docs/evidence/optimization-20261026/existing-data-rehearsal-plan.json
```

候选镜像构建完成后，由主任务按固定镜像 ID 串行执行下列命令。不要填可漂移的 `latest` 标签。恢复目录必须全新；前次资源保留时，下一次使用新的目录，不覆盖报告或恢复文件。

```powershell
$tripCandidateImageId = $env:TRIP_CANDIDATE_IMAGE_ID
if ($tripCandidateImageId -notmatch '^sha256:[a-f0-9]{64}$') {
  throw '请先由主任务设置已构建候选的固定镜像 ID'
}
python backend/scripts/existing_data_rehearsal.py `
  --backup E:\project\langchain-trip-planner\backups\current-20261025-improvement `
  --output E:\recovery\trip-opt-20261026-existing-data `
  --candidate-image $tripCandidateImageId `
  --daily-project langchain-trip-planner `
  --execute `
  --report docs/evidence/optimization-20261026/existing-data-rehearsal-result.json
if ($LASTEXITCODE -ne 0) { throw '演练未通过；读取报告并保留全部资源' }
```

网络地址池不足时可分别传入 `--upgrade-subnet` 和 `--rollback-subnet`。两个 CIDR 必须互不重叠，并由恢复工具核对现有 Docker 网络；不要照抄未经核对的地址。

演练 A 在独立 PostgreSQL、独立目录与独立网络恢复原快照，验证原镜像就绪，再停止并唯一重命名基线后端容器。原表原列摘要与三个解压目录逐文件清单全部匹配后，才为克隆数据库和目录登记同一个数据集 ID；已有不同 ID 会拒绝，不覆盖标记。

正确候选启动前，额外保留两个候选负例容器：数据库 ID 错配、单独的上传目录 ID 错配。负例必须以对应身份错误非零退出；其他启动故障不算验证通过。数据库 ID、原目录内容和错配目录哨兵不得被覆盖，完整 Flyway 历史不得推进。正确候选强制 `APP_ENV=production`、`DATASET_ENROLL_NEW=false`，不使用 development 绕过。候选完成迁移后，核对原业务列内容与既有 Flyway 记录。

演练 B 从同一备份再恢复另一套全新的 PostgreSQL 和旧后端，旧后端就绪后再次核对完整原快照摘要。这才提供所选恢复点的数据回退证据。成功路径总计保留 7 个容器和 2 个网络，包括 2 个预期启动失败的负例；失败路径也保留已创建资源，并逐项记录。

保留的旧后端容器若重新启动，仍连接 A 中已经升级的数据库；这只可用于单独的兼容性观察，不能称为数据回滚。默认不会自动尝试这一步。

报告记录 A/B 各自耗时、总耗时、精确容器与网络、镜像 ID/标签中的源码修订，以及日常四服务的前后 ID、镜像、挂载与运行状态。前后相同不代表连续监控；脚本自身只对新恢复资源执行写入。当前演练只启动禁用 workers 的 Java 与 PostgreSQL；Agent、检索、前端和实际角色流程仍需候选实例另行验收。Chroma 与 runtime 归档哈希通过不等于对应能力已经可用。

## 数据集登记与部署挂载

数据库、上传目录、Agent runtime 与 Chroma 使用一致的 `DATASET_ID`，生产启动拒绝已知不匹配或未经登记的持久卷。数据集标记识别归属，不识别恢复时点；同一数据集的不同时间快照仍须成套恢复。

已有数据登记使用 `enroll_dataset.py`。检查模式仍要求写入者已停止，因为实际文件清单与数据库摘要必须稳定。停止日常服务属于本轮协调部署窗口，不能为了运行检查自行提前停机。

```powershell
python backend/scripts/enroll_dataset.py `
  --project langchain-trip-planner `
  --backup E:\backups\trip-before-upgrade-FRESH `
  --runtime E:\project\langchain-trip-planner\deploy\runtime
```

只有受控窗口内，使用刚验证且与当前停写数据一致的备份，才在同样命令后追加 `--execute`。登记前检查原数据库名/用户、备份原表原列内容、三套文件卷身份和逐文件 SHA-256 清单；新增、缺失或变更的实际文件都会拒绝。仅根目录同 ID 的 `.dataset-id` 是可忽略的登记元数据，不能据此跳过其他文件。

新备份包含 PostgreSQL 数据挂载身份。旧备份缺少该字段时，报告明确 `postgres_mount_identity_compared=false`，只能引用原表列摘要比对，不能声称核对过缺失的卷证据。摘要/哈希不代替恢复演练。不要在登记失败后自动改 ID 或覆盖其他卷的标记。

## 自动备份与原状态恢复

`automated_maintenance.py` 默认只检查；只有 `--execute` 才进入维护窗口。它先保存原状态，停写后备份，独立尝试恢复每个原本运行的应用服务，确认对应容器真正就绪，并把原本停止的 PostgreSQL 恢复为停止状态。任一步恢复失败，仍继续尝试其他恢复步骤，结构化报告保留错误阶段与服务，进程非零退出。

同一项目的备份、检查和调度恢复共用非阻塞跨进程锁，获取锁先于 Docker 状态读取及变更，并覆盖停写、归档、原状态恢复与成功心跳。另一个维护进程持锁时直接非零退出；进程退出由操作系统释放锁，但锁文件永久保留，不能手动删除重建来绕开锁。默认位置是当前执行账号临时目录下的 `trip-planner-maintenance-locks`，Windows 使用字节锁，Unix 使用 `flock`。多个账号共享 Docker daemon 时必须统一设置可写的 `TRIP_MAINTENANCE_LOCK_DIR`，否则不能声称跨账号互斥。计划任务的 `IgnoreNew` 只是额外防重，不能代替这个锁。

```powershell
python backend/scripts/automated_maintenance.py backup `
  --project langchain-trip-planner `
  --backup-root E:\backups\trip `
  --output E:\backups\trip\maintenance-inspection.json
```

归档有效与维护成功是两个结论。恢复运行状态或 readiness 失败时，不刷新成功心跳，也不把已生成的有效归档包装成整个维护成功。实际调度器/系统任务需要调用明确的 `--execute` 命令，并检查退出码和报告；仅保存脚本不等于定时备份已经启用。

`install-backup-task.ps1` 生成的实际命令包含 `--output <BackupRoot>\last-run.json`，失败报告同样落盘；`last-success.json` 只在整个维护成功后更新。安装脚本默认只展示配置，追加 `-Install` 才安装，不覆盖已有同名任务。当前交互账号、Docker Desktop 和备份目录写入能力都是实际执行前提。

默认 RPO 目标为 86400 秒、RTO 目标为 3600 秒，它们是规划目标。独立克隆耗时不包含故障发现、值班响应、生产切换与异机传输，不能直接证明生产 RTO。正式迁移前仍要验证当时最新的成套备份和回退依据。

## 副本位置与异机证据

可通过 `--replica-root` 配置新的目标目录。副本以全新目录写入并验证清单，不同步删除目标。UNC 的 `localhost`、回环 IP、本机名与本机 FQDN 不视为远端目标。其他 UNC 只记录“已配置远端目标、复制字节已验证”；`off_host=false`、`failure_domain_verified=false` 明确保留真实异机/独立故障域演练的外部缺口，不更新伪造的异机成功心跳。

## 调度所有权故障

```powershell
python backend/scripts/automated_maintenance.py recover-scheduler `
  --project langchain-trip-planner `
  --output E:\backups\trip\scheduler-inspection.json
```

探针固定从所选后端容器访问自身 `/readyz`，只有明确的 `SCHEDULER_OWNERSHIP_LOST` 才允许受控恢复。原本停止的后端不会自动重启。兼容参数 `--base-url` 若提供，须为所选前端实际发布端口的本机地址；任意外部 URL 或其他项目端口不能触发重启。执行恢复同样要由主任务进入已分配的窗口，实际就绪前不报告恢复成功。

## 告警、容量和保留

生产 monitoring profile 包含 Prometheus、Alertmanager 与受控本地持久收件箱；`operational_alert_receiver.py` 限制请求体、记录字段、文件大小并去重。它证明本地接收能力，不自动给真实个人发送邮件或通知。最终应单独验收规则触发、送达和去重；外部接收端未配置时保留外部缺口。

Compose 为数据库、Java、Agent、前端和监控配置 CPU、内存、PID 与日志轮转上限。这些值限制故障影响，不证明实际峰值容量或长期稳定性。容量调整需结合实际资源、队列、连接池和受影响链路证据。

`OperationalRetention` 分批处理过期快照和超保留期终态任务载荷，保留任务身份、状态、恢复血缘和调用预算。成功 Outbox 历史保留每个实体最新成功，失败/待处理工作与必要删除水位不按成功历史清理。载荷到期后恢复返回明确原因。数据保留不表示可以删除业务库、预算账本、审计或用户备份。

## 资源保留与验收缺口

上述恢复与演练工具不会自动删除容器、网络、镜像或恢复材料。只有协调窗口另行授权、归属与回退依据均已核对后，主任务才能按精确清单处理冗余对象；不使用宽泛 prune、`down -v` 或文件永久删除。

截至计划阶段，实际已有数据升级/回退耗时、Agent/前端恢复可用性、真实异机副本、最终运行镜像与源码关系、当前 SHA 的全部 CI 仍需分别提供结果。不得把本手册、预检通过或纯测试数量改写为这些验收已完成。
