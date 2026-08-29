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
            # Uvicorn --reload 会派生子进程；通过独立 taskkill 进程清理其进程树，
            # 避免把 Ctrl+C 或 taskkill 输出混入当前前端终端。
            Start-Process -FilePath 'taskkill.exe' -ArgumentList '/PID', "$($process.Id)", '/T', '/F' -Wait -NoNewWindow -ErrorAction SilentlyContinue
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
    $nodeCommand = Get-Command 'node.exe' -ErrorAction Stop
    $viteEntry = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path $viteEntry)) { throw '未找到 Vite，请先在 frontend 目录执行 npm install。' }
    # 后端 .env 可能只允许 localhost，而本脚本将 Vite 绑定在 127.0.0.1。
    # 仅覆盖本次本地子进程的 CORS 配置，避免浏览器把跨域预检失败显示为 Network Error。
    $env:CORS_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
    Write-Host "使用 Conda 环境：$env:CONDA_DEFAULT_ENV" -ForegroundColor Cyan
    Write-Host '启动后端：http://localhost:9000' -ForegroundColor Cyan
    $children += Start-Process -FilePath $condaPython -ArgumentList 'run.py' -WorkingDirectory $backendDir -PassThru -NoNewWindow

    Write-Host '启动前端：http://127.0.0.1:5173' -ForegroundColor Cyan
    Write-Host '前端服务保持在当前终端。按一次 Ctrl+C 将停止前端并清理后端，随后回到提示符。' -ForegroundColor Green
    # 直接运行 Vite 的 Node 入口，不经过 npm.cmd 批处理，避免 Ctrl+C 弹出“终止批处理操作”确认。
    Push-Location $frontendDir
    try {
        & $nodeCommand.Source $viteEntry --host 127.0.0.1
    }
    finally {
        Pop-Location
    }
}
finally {
    Stop-LocalServices
}
