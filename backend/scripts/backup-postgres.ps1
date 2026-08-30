param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\data\backups")
)

$ErrorActionPreference = "Stop"

function Get-PgTool([string]$Name) {
    if ($env:PG_BIN) {
        $candidate = Join-Path $env:PG_BIN "$Name.exe"
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "未找到 $Name。请将 PostgreSQL bin 加入 PATH，或设置 PG_BIN。" }
    return $command.Source
}

function Get-DatabaseConnection([string]$Path) {
    $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -Last 1
    if (-not $line) { throw "未在 $Path 找到 DATABASE_URL。" }
    $value = ($line -replace '^\s*DATABASE_URL\s*=\s*', '').Trim()
    $uri = [Uri]($value -replace '^postgresql\+psycopg://', 'postgresql://')
    $separator = $uri.UserInfo.IndexOf(':')
    if ($separator -lt 1) { throw "DATABASE_URL 缺少用户名或密码。" }
    return @{
        Host = $uri.Host; Port = $(if ($uri.IsDefaultPort) { 5432 } else { $uri.Port })
        User = [Uri]::UnescapeDataString($uri.UserInfo.Substring(0, $separator))
        Password = [Uri]::UnescapeDataString($uri.UserInfo.Substring($separator + 1))
        Database = $uri.AbsolutePath.TrimStart('/')
    }
}

$connection = Get-DatabaseConnection $EnvFile
$pgDump = Get-PgTool "pg_dump"
$psql = Get-PgTool "psql"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $OutputDirectory ("$($connection.Database)-$timestamp.dump")
$manifestPath = "$backupPath.manifest.json"
$previousPassword = $env:PGPASSWORD

try {
    $env:PGPASSWORD = $connection.Password
    & $pgDump -h $connection.Host -p $connection.Port -U $connection.User -Fc --no-owner -f $backupPath $connection.Database
    if ($LASTEXITCODE -ne 0) { throw "pg_dump 失败。" }

    $tableCounts = @{}
    foreach ($table in @('users', 'trip_records', 'rag_sync_jobs')) {
        $count = & $psql -h $connection.Host -p $connection.Port -U $connection.User -d $connection.Database -Atc "SELECT count(*) FROM $table"
        if ($LASTEXITCODE -ne 0) { throw "读取 $table 行数失败。" }
        $tableCounts[$table] = [int]$count
    }
    @{ database = $connection.Database; created_at = (Get-Date).ToUniversalTime().ToString('o'); tables = $tableCounts } |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Write-Output "备份完成: $backupPath"
    Write-Output "校验清单: $manifestPath"
}
finally {
    $env:PGPASSWORD = $previousPassword
}
