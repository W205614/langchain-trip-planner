param(
  [string]$TaskName = 'TripPlanner-AutomatedBackup',
  [string]$TaskPath = '\',
  [switch]$UpdateExistingAction
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath "$PSScriptRoot\..").Path
$legacy = [IO.Path]::GetFullPath((Join-Path $projectRoot 'backend\scripts\automated_maintenance.py'))
$runner = [IO.Path]::GetFullPath((Join-Path $projectRoot 'tools\windows_backup_runner.pyw'))
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'Silent runner is unavailable.' }
$tasks = @(Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath)
if ($tasks.Count -ne 1 -or $null -eq $tasks[0]) { throw 'Migration requires exactly one existing task.' }
$task = $tasks[0]
$actions = @($task.Actions)
if ($actions.Count -ne 1 -or $null -eq $actions[0]) { throw 'Migration requires exactly one reviewed existing Action.' }
$existing = $actions[0]
if ([IO.Path]::GetFileName($existing.Execute).ToLowerInvariant() -ne 'pythonw.exe') {
  throw 'Existing Action must already use the reviewed pythonw.exe; executable migration is not authorized.'
}
if (-not (Test-Path -LiteralPath $existing.Execute -PathType Leaf)) { throw 'Existing pythonw executable is unavailable.' }
$match = [regex]::Match($existing.Arguments, '^(?<prefix>\s*)(?:"(?<script>[^"]+)"|(?<script>\S+))(?<tail>\s+backup(?:\s.*)?)$', [Text.RegularExpressions.RegexOptions]::Singleline)
if (-not $match.Success) { throw 'Existing Action is not the reviewed backup command shape.' }
$script = [IO.Path]::GetFullPath($match.Groups['script'].Value)
if (-not ([string]::Equals($script, $legacy, [StringComparison]::OrdinalIgnoreCase) -or
          [string]::Equals($script, $runner, [StringComparison]::OrdinalIgnoreCase))) {
  throw 'Existing script is outside this project''s reviewed maintenance/runner paths.'
}
$tail = $match.Groups['tail'].Value
if ($tail -notmatch '(?<!\S)--execute(?!\S)') { throw 'Existing backup Action lacks the reviewed execute flag.' }
$arguments = $match.Groups['prefix'].Value + '"' + $runner + '"' + $tail
$preview = [pscustomobject]@{ TaskName=$TaskName; TaskPath=$TaskPath; Executable=$existing.Execute;
  ExistingArguments=$existing.Arguments; ProposedArguments=$arguments;
  ExistingWorkingDirectory=$existing.WorkingDirectory; ProposedWorkingDirectory=$projectRoot;
  ChangesOnly='Action script token and Action working directory; original argument tail/executable retained';
  Applied=$false }
if (-not $UpdateExistingAction) { $preview; return }
$before = [xml](Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath)
$preserved = @{}
foreach ($name in @('Principals','Settings','Triggers')) {
  $preserved[$name] = $before.DocumentElement.SelectSingleNode("*[local-name()='$name']").OuterXml
}
$action = New-ScheduledTaskAction -Execute $existing.Execute -Argument $arguments -WorkingDirectory $projectRoot
# No credentials, principal, settings, trigger, schedule, registration or run call.
Set-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action | Out-Null
$after = [xml](Export-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath)
foreach ($name in $preserved.Keys) {
  if ($after.DocumentElement.SelectSingleNode("*[local-name()='$name']").OuterXml -cne $preserved[$name]) {
    throw "Task $name changed unexpectedly; preserve the evidence and review before running."
  }
}
$preview.Applied = $true
$preview
