param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$TargetDatabase = "trip_planner_restore_verify",
    [switch]$ReplaceTarget
)

$ErrorActionPreference = "Stop"
if ($TargetDatabase -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') { throw "TargetDatabase 名称不合法。" }
if (-not (Test-Path -LiteralPath $BackupPath)) { throw "备份文件不存在: $BackupPath" }
$manifestPath = "$BackupPath.manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "缺少备份校验清单: $manifestPath" }

function Get-PgTool([string]$Name) {
    if ($env:PG_BIN) {
        $candidate = Join-Path $env:PG_BIN "$Name.exe"
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "未找到 $Name。请将 PostgreSQL bin 加入 PATH，或设置 PG_BIN。" }
    return $command.Source
}

$line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -Last 1
if (-not $line) { throw "未在 $EnvFile 找到 DATABASE_URL。" }
$databaseUrl = (($line -replace '^\s*DATABASE_URL\s*=\s*', '').Trim() -replace '^postgresql\+psycopg://', 'postgresql://')
$match = [regex]::Match($databaseUrl, '^postgresql://(?<user>[^:]+):(?<password>[^@]+)@(?<host>[^:/]+)(?::(?<port>\d+))?/(?<database>[^?]+)')
if (-not $match.Success) { throw "DATABASE_URL 格式不受恢复脚本支持。" }
$user = [Uri]::UnescapeDataString($match.Groups['user'].Value)
$password = [Uri]::UnescapeDataString($match.Groups['password'].Value)
$dbHost = $match.Groups['host'].Value
$port = if ($match.Groups['port'].Success) { [int]$match.Groups['port'].Value } else { 5432 }
$psql = Get-PgTool "psql"
$pgRestore = Get-PgTool "pg_restore"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$previousPassword = $env:PGPASSWORD

try {
    $env:PGPASSWORD = $password
    $existsOutput = & $psql -h $dbHost -p $port -U $user -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$TargetDatabase'"
    $exists = if ($null -eq $existsOutput) { "" } else { ([string]$existsOutput).Trim() }
    if ($exists -and -not $ReplaceTarget) {
        throw "目标数据库 $TargetDatabase 已存在。确认可替换后加 -ReplaceTarget。"
    }
    if ($exists) {
        & $psql -h $dbHost -p $port -U $user -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TargetDatabase' AND pid <> pg_backend_pid();"
        if ($LASTEXITCODE -ne 0) { throw "终止旧恢复校验连接失败。" }
        $dropSql = "DROP DATABASE `"$TargetDatabase`";"
        & $psql -h $dbHost -p $port -U $user -d postgres -c $dropSql
        if ($LASTEXITCODE -ne 0) { throw "删除旧恢复校验数据库失败。" }
    }
    $createSql = "CREATE DATABASE `"$TargetDatabase`";"
    & $psql -h $dbHost -p $port -U $user -d postgres -c $createSql
    if ($LASTEXITCODE -ne 0) { throw "创建恢复校验数据库失败。" }
    & $pgRestore -h $dbHost -p $port -U $user -d $TargetDatabase --no-owner --clean --if-exists $BackupPath
    if ($LASTEXITCODE -ne 0) { throw "pg_restore 失败。" }

    foreach ($property in $manifest.tables.PSObject.Properties) {
        if ($property.Name -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') { throw "清单表名不合法。" }
        $actualOutput = & $psql -h $dbHost -p $port -U $user -d $TargetDatabase -Atc "SELECT count(*) FROM $($property.Name)"
        $actual = if ($null -eq $actualOutput) { "" } else { ([string]$actualOutput).Trim() }
        if ([int]$actual -ne [int]$property.Value) {
            throw "表 $($property.Name) 行数不一致：expected=$($property.Value), actual=$actual"
        }
    }
    Write-Output "恢复并校验成功: $TargetDatabase"
}
finally {
    $env:PGPASSWORD = $previousPassword
}
