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
$script:stopRequested = $false
$cancelHandler = [ConsoleCancelEventHandler]{
    param($sender, $eventArgs)
    # 由脚本统一清理进程树，避免 Ctrl+C 只被 Uvicorn reload 子进程接收。
    $eventArgs.Cancel = $true
    $script:stopRequested = $true
}

function Stop-LocalServices {
    foreach ($process in $children) {
        if ($null -ne $process -and -not $process.HasExited) {
            Write-Host "停止 $($process.ProcessName) (PID $($process.Id))..." -ForegroundColor Yellow
            # Uvicorn --reload 与 npm 都会派生子进程；按进程树终止才能一次 Ctrl+C 全部退出。
            & taskkill.exe /PID $process.Id /T /F *> $null
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
    # 后端 .env 可能只允许 localhost，而本脚本将 Vite 绑定在 127.0.0.1。
    # 仅覆盖本次本地子进程的 CORS 配置，避免浏览器把跨域预检失败显示为 Network Error。
    $env:CORS_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
    [Console]::add_CancelKeyPress($cancelHandler)

    Write-Host "使用 Conda 环境：$env:CONDA_DEFAULT_ENV" -ForegroundColor Cyan
    Write-Host '启动后端：http://localhost:9000' -ForegroundColor Cyan
    $children += Start-Process -FilePath $condaPython -ArgumentList 'run.py' -WorkingDirectory $backendDir -PassThru -NoNewWindow

    Write-Host '启动前端：http://localhost:5173' -ForegroundColor Cyan
    $children += Start-Process -FilePath $npmCommand.Source -ArgumentList 'run','dev','--','--host','127.0.0.1' -WorkingDirectory $frontendDir -PassThru -NoNewWindow

    Write-Host '服务正在前台运行。按 Ctrl+C 可同时停止前后端。' -ForegroundColor Green
    while (-not $script:stopRequested) {
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
    [Console]::remove_CancelKeyPress($cancelHandler)
    Stop-LocalServices
}
