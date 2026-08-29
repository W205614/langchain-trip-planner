<#
本地前后端联调入口。请先激活 Conda 环境：
conda activate langchain-trip-planner
再运行：powershell -ExecutionPolicy Bypass -File .\start-local.ps1
前端保持在当前终端前台运行；按一次 Ctrl+C 会停止前端、清理后端进程树并返回 PowerShell 提示符。
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
    Write-Host "使用 Conda 环境：$env:CONDA_DEFAULT_ENV" -ForegroundColor Cyan
    Write-Host '启动后端：http://localhost:9000' -ForegroundColor Cyan
    $children += Start-Process -FilePath $condaPython -ArgumentList 'run.py' -WorkingDirectory $backendDir -PassThru -NoNewWindow

    Write-Host '启动前端：http://127.0.0.1:5173' -ForegroundColor Cyan
    Write-Host '前端服务保持在当前终端。按一次 Ctrl+C 将停止前端并清理后端，随后回到提示符。' -ForegroundColor Green
    # 不把 npm 再作为后台进程：Ctrl+C 只会中断当前前台 Vite，再进入 finally 清理后端。
    Push-Location $frontendDir
    try {
        & $npmCommand.Source run dev -- --host 127.0.0.1
    }
    finally {
        Pop-Location
    }
}
finally {
    Stop-LocalServices
}
