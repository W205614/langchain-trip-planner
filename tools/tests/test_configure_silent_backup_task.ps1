# Pure mocked Task Scheduler test. Never registers, runs, starts or stops a task.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$migration = Join-Path $root 'tools\configure_silent_backup_task.ps1'
$pythonw = Join-Path $root '.cache\improvement-venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Existing host pythonw fixture unavailable.' }
$legacy = Join-Path $root 'backend\scripts\automated_maintenance.py'
$tail = ' backup --execute --project langchain-trip-planner --backup-root "E:\private backups\trip" --output "E:\private backups\trip\last-run.json"'
$global:TripSilentTaskFixture = [pscustomobject]@{ Actions=@([pscustomobject]@{ Execute=$pythonw; Arguments=('"'+$legacy+'"'+$tail); WorkingDirectory=$root }) }
$global:TripSilentTaskSetCalls = 0
function Get-ScheduledTask { param($TaskName,$TaskPath); $global:TripSilentTaskFixture }
function Export-ScheduledTask { param($TaskName,$TaskPath); '<Task><Principals><Principal><UserId>fixture-user</UserId><RunLevel>LeastPrivilege</RunLevel><LogonType>InteractiveToken</LogonType></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings><Triggers><CalendarTrigger><StartBoundary>2026-10-27T03:00:00</StartBoundary></CalendarTrigger></Triggers></Task>' }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory); [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function Set-ScheduledTask {
  param($TaskName,$TaskPath,$Action)
  if (@($PSBoundParameters.Keys | Where-Object {$_ -notin @('TaskName','TaskPath','Action')}).Count) { throw 'Unexpected mutation parameter' }
  $global:TripSilentTaskSetCalls++;$global:TripSilentTaskFixture.Actions=@($Action);$global:TripSilentTaskFixture
}
$preview = & $migration
if ($global:TripSilentTaskSetCalls -ne 0 -or $preview.Applied) { throw 'Default preview mutated the task.' }
if (-not $preview.ProposedArguments.EndsWith($tail,[StringComparison]::Ordinal)) { throw 'Original argument tail changed.' }
$applied = & $migration -UpdateExistingAction
if ($global:TripSilentTaskSetCalls -ne 1 -or -not $applied.Applied -or $global:TripSilentTaskFixture.Actions[0].Execute -cne $pythonw) { throw 'Action-only migration failed.' }
if (-not $global:TripSilentTaskFixture.Actions[0].Arguments.EndsWith($tail,[StringComparison]::Ordinal)) { throw 'Backup arguments changed.' }
$global:TripSilentTaskFixture.Actions[0].Arguments = '"C:\other\maintenance.py"' + $tail
$rejected = $false
try { & $migration -UpdateExistingAction | Out-Null } catch { $rejected=$true }
if (-not $rejected -or $global:TripSilentTaskSetCalls -ne 1) { throw 'Unreviewed script was not rejected before mutation.' }
[pscustomobject]@{passed=$true;cases=3;taskSchedulerMocked=$true;realTaskMutations=0;dockerCalls=0}
