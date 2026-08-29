<#
本地前后端联调入口。请先激活 Conda 环境：
conda activate langchain-trip-planner
再运行：powershell -ExecutionPolicy Bypass -File .\start-local.ps1
脚本保持前台运行：按 Ctrl+C 会停止它启动的两个子进程，不会遗留后台服务。
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$children = @()

function Stop-LocalServices {
    foreach ($process in $children) {
        if ($null -ne $process -and -not $process.HasExited) {
            Write-Host "停止 $($process.ProcessName) (PID $($process.Id))..." -ForegroundColor Yellow
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    if ($env:CONDA_DEFAULT_ENV -ne 'langchain-trip-planner') {
        throw '请先执行 conda activate langchain-trip-planner，再运行本脚本。'
    }
    if (-not (Test-Path (Join-Path $backendDir 'run.py'))) { throw '未找到 backend/run.py' }
    if (-not (Test-Path (Join-Path $frontendDir 'package.json'))) { throw '未找到 frontend/package.json' }

    $condaPython = Join-Path $env:CONDA_PREFIX 'python.exe'
    if (-not (Test-Path $condaPython)) { throw "未找到当前 Conda 环境的 Python：$condaPython" }
    # npm 在 Windows 常优先解析为 npm.ps1；Start-Process 不能直接运行该脚本，必须选 npm.cmd。
    $npmCommand = Get-Command 'npm.cmd' -ErrorAction Stop

    Write-Host "使用 Conda 环境：$env:CONDA_DEFAULT_ENV" -ForegroundColor Cyan
    Write-Host '启动后端：http://localhost:9000' -ForegroundColor Cyan
    $children += Start-Process -FilePath $condaPython -ArgumentList 'run.py' -WorkingDirectory $backendDir -PassThru -NoNewWindow

    Write-Host '启动前端：http://localhost:5173' -ForegroundColor Cyan
    $children += Start-Process -FilePath $npmCommand.Source -ArgumentList 'run','dev','--','--host','127.0.0.1' -WorkingDirectory $frontendDir -PassThru -NoNewWindow

    Write-Host '服务正在前台运行。按 Ctrl+C 可同时停止前后端。' -ForegroundColor Green
    while ($true) {
        Start-Sleep -Milliseconds 500
        foreach ($process in $children) {
            if ($process.HasExited) {
                throw "$($process.ProcessName) 已退出，正在停止其他服务。"
            }
        }
    }
}
catch {
    if ($_.Exception.Message -notmatch '已退出') { Write-Error $_ }
}
finally {
    Stop-LocalServices
}
