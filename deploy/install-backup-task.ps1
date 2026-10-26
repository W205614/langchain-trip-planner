param(
  [Parameter(Mandatory=$true)][string]$PythonPath,
  [string]$BackupRoot = "$PSScriptRoot\..\backups",
  [string]$ReplicaRoot = "",
  [string]$At = "03:00",
  [switch]$Install
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath "$PSScriptRoot\..").Path
$pythonResolved = (Resolve-Path -LiteralPath $PythonPath).Path
$scriptPath = Join-Path $projectRoot 'backend\scripts\automated_maintenance.py'
$backupResolved = [IO.Path]::GetFullPath($BackupRoot)
$lastRunReport = Join-Path $backupResolved 'last-run.json'
$arguments = '"' + $scriptPath + '" backup --execute --backup-root "' + $backupResolved + '" --output "' + $lastRunReport + '"'
if ($ReplicaRoot) { $arguments += ' --replica-root "' + $ReplicaRoot + '"' }
$taskName = 'TripPlanner-AutomatedBackup'
if (-not $Install) { [pscustomobject]@{ Task=$taskName; Executable=$pythonResolved; Arguments=$arguments; LastRunReport=$lastRunReport; At=$At; Installed=$false }; return }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Existing task must be reviewed; this installer never overwrites it.' }
$action = New-ScheduledTaskAction -Execute $pythonResolved -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Select-Object TaskName,State
# Docker Desktop and the current user session must be available. The backup script restores original service state.
