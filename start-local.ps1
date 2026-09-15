param([switch]$Build)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    foreach ($name in @('postgres', 'business', 'agent')) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "deploy/runtime/$name.env"))) {
            throw '请先按 README 准备 deploy/runtime 的独立配置，并完成旧数据迁移。'
        }
    }
    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw '部署配置校验失败。' }
    $arguments = @('compose', 'up', '-d', '--wait')
    if ($Build) { $arguments += '--build' }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw '服务未全部就绪，请检查 Docker 日志。' }
    Write-Host '服务入口：http://localhost:8080' -ForegroundColor Green
    Write-Host '若处于迁移维护窗口，WORKERS_ENABLED=false 会保持任务暂停。'
} finally { Pop-Location }
