param(
    [ValidateSet('Preview', 'Snapshot', 'Apply', 'Verify', 'EnsureBackupRoot')]
    [string]$Mode = 'Preview',
    [string]$AuditRoot,
    [string]$ManifestHash,
    [string]$BackupRoot
)
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowedSids = @($currentSid, 'S-1-5-18', 'S-1-5-32-544')
$relativeRoots = @('backups', '.codex-optimization', 'deploy\runtime', 'backend\.env')
$maximumBytes = 8GB
$maximumItems = 2000

function Get-ExactItem([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are outside the private ACL scope' }
    if (-not $item.PSIsContainer) {
        if (-not ('TripPrivateFileLinks' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class TripPrivateFileLinks {
 [StructLayout(LayoutKind.Sequential)] struct Info {
  public uint attrs; public System.Runtime.InteropServices.ComTypes.FILETIME created,accessed,written;
  public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern SafeFileHandle CreateFileW(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle file,out Info info);
 public static uint Count(string path) {
  using(var file=CreateFileW(path,0,7,IntPtr.Zero,3,0,IntPtr.Zero)) {
   if(file.IsInvalid)throw new Win32Exception(Marshal.GetLastWin32Error());
   Info info; if(!GetFileInformationByHandle(file,out info))throw new Win32Exception(Marshal.GetLastWin32Error());
   return info.links;
  }
 }
}
'@
        }
        if ([TripPrivateFileLinks]::Count($item.FullName) -ne 1) { throw 'Hard links are outside the exact private path scope' }
    }
    return $item
}

function Get-OwnerSid($Acl) {
    return $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
}

function Get-PrivateAccess($Acl) {
    return @($Acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) | ForEach-Object {
        [ordered]@{ sid = $_.IdentityReference.Value; type = $_.AccessControlType.ToString();
            rights = [long]$_.FileSystemRights; inherited = $_.IsInherited;
            inheritance = [int]$_.InheritanceFlags; propagation = [int]$_.PropagationFlags }
    })
}

function Assert-PrivateAcl([string]$Path) {
    $item = Get-ExactItem $Path
    $acl = Get-Acl -LiteralPath $item.FullName
    if ((Get-OwnerSid $acl) -ne $currentSid) { throw 'Private path is not owned by the current user; ownership is never taken' }
    $access = @(Get-PrivateAccess $acl)
    if ($access.Count -eq 0 -or @($access | Where-Object { $_.sid -notin $allowedSids -or $_.type -ne 'Allow' }).Count) {
        throw 'Private path contains access outside the current user, SYSTEM and Administrators'
    }
    foreach ($sid in $allowedSids) {
        $effective = @($access | Where-Object {
            $_.sid -eq $sid -and -not ($_.propagation -band [int][Security.AccessControl.PropagationFlags]::InheritOnly) -and
            ($_.rights -band [long][Security.AccessControl.FileSystemRights]::FullControl) -eq [long][Security.AccessControl.FileSystemRights]::FullControl
        })
        if ($effective.Count -eq 0) { throw 'A required private principal lacks effective FullControl' }
    }
    return $acl
}

function Set-PrivateAcl([string]$Path) {
    $item = Get-ExactItem $Path
    $acl = Get-Acl -LiteralPath $item.FullName
    if ((Get-OwnerSid $acl) -ne $currentSid) { throw 'Refuse to modify a path not owned by the current user' }
    # Native DACL-only flags avoid Set-Acl's broader descriptor/SACL privilege
    # request on an already protected object. No token privilege is enabled.
    if (-not ('TripPrivateDaclOnly' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class TripPrivateDaclOnly {
 [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint revision, out IntPtr sd, out uint size);
 [DllImport("advapi32.dll", SetLastError=true)]
 static extern bool GetSecurityDescriptorDacl(IntPtr sd, out bool present, out IntPtr dacl, out bool defaulted);
 [DllImport("advapi32.dll", CharSet=CharSet.Unicode)]
 static extern uint SetNamedSecurityInfoW(string name, uint kind, uint information, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
 public static void Apply(string path, string sddl) {
  IntPtr sd; uint size;
  if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,out sd,out size)) throw new Win32Exception(Marshal.GetLastWin32Error());
  try {
   bool present,defaulted; IntPtr dacl;
   if(!GetSecurityDescriptorDacl(sd,out present,out dacl,out defaulted) || !present || dacl==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
   uint result=SetNamedSecurityInfoW(path,1,0x80000004,IntPtr.Zero,IntPtr.Zero,dacl,IntPtr.Zero);
   if(result!=0)throw new Win32Exception((int)result);
  } finally { LocalFree(sd); }
 }
}
'@
    }
    $inherit = if ($item.PSIsContainer) { 'OICI' } else { '' }
    $sddl = 'D:P' + (($allowedSids | ForEach-Object { "(A;$inherit;FA;;;$_)" }) -join '')
    [TripPrivateDaclOnly]::Apply($item.FullName, $sddl)
    [void](Assert-PrivateAcl $item.FullName)
}

function Get-Scope {
    $all = @()
    foreach ($relative in $relativeRoots) {
        $path = Join-Path $workspace $relative
        $stack = [Collections.Generic.Stack[string]]::new()
        $stack.Push($path)
        while ($stack.Count) {
            $item = Get-ExactItem $stack.Pop()
            if (-not $item.FullName.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Private scope escaped the workspace' }
            $all += $item
            if ($all.Count -gt $maximumItems) { throw 'Private scope exceeds the reviewed item bound' }
            if ($item.PSIsContainer) {
                foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) { $stack.Push($child.FullName) }
            }
        }
    }
    $bytes = [long]0
    foreach ($item in $all) { if (-not $item.PSIsContainer) { $bytes += $item.Length } }
    if ($bytes -gt $maximumBytes) { throw 'Private scope exceeds the reviewed byte bound' }
    return @($all | Sort-Object FullName)
}

function Hash-Content([string]$Path) {
    # Streaming SHA256; file contents never enter output or a report.
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-PrivateJson([string]$Path, $Value) {
    $parent = Split-Path -Parent $Path
    [void](Assert-PrivateAcl $parent)
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding utf8
    [void](Assert-PrivateAcl $Path)
}

function Resolve-AuditRoot {
    if (-not $AuditRoot) { throw 'An explicit private audit directory is required' }
    $full = [IO.Path]::GetFullPath($AuditRoot)
    if ($full.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase) -or $full -eq $workspace) {
        throw 'Audit output must remain outside the frozen private scope and checkout'
    }
    if (-not (Test-Path -LiteralPath $full)) { [void][IO.Directory]::CreateDirectory($full) }
    Set-PrivateAcl $full
    return $full
}

function Read-Manifest([string]$Root) {
    $path = Join-Path $Root 'original-manifest.json'
    if (-not $ManifestHash -or (Hash-Content $path) -ne $ManifestHash.ToLowerInvariant()) { throw 'Explicit original manifest SHA256 does not match' }
    $data = Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json
    if ($data.current_user_sid -ne $currentSid -or $data.workspace -ne $workspace) { throw 'Manifest user/workspace mismatch' }
    if (($data.relative_roots | ConvertTo-Json -Compress) -ne ($relativeRoots | ConvertTo-Json -Compress)) { throw 'Manifest does not contain the four reviewed roots' }
    return $data
}

try {
    if ($Mode -eq 'EnsureBackupRoot') {
        if (-not $BackupRoot) { throw 'Backup root is required' }
        $base = Join-Path $workspace 'backups'
        $full = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\', '/')
        if ($full -ne $base -and -not $full.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup root is outside this project private backup directory' }
        [void](Assert-PrivateAcl $base)
        $current = $base
        $tail = $full.Substring($base.Length).TrimStart('\', '/')
        foreach ($part in @($tail -split '[\\/]' | Where-Object { $_ })) {
            $current = Join-Path $current $part
            if (-not (Test-Path -LiteralPath $current)) { [void][IO.Directory]::CreateDirectory($current) }
            [void](Assert-PrivateAcl $current)
        }
        @{ passed = $true; mode = $Mode; current_user_owned = $true; allowed_principals = 3;
            before_log_creation = $true; powershell_major = $PSVersionTable.PSVersion.Major } | ConvertTo-Json -Compress
        exit 0
    }
    if ($Mode -eq 'Preview') {
        $scope = @(Get-Scope)
        @{ passed = $true; mode = 'metadata_only'; files = @($scope | Where-Object { -not $_.PSIsContainer }).Count;
            directories = @($scope | Where-Object { $_.PSIsContainer }).Count; acl_changes = 0 } | ConvertTo-Json -Compress
        exit 0
    }
    $privateAudit = Resolve-AuditRoot
    if ($Mode -eq 'Snapshot') {
        $records = @()
        foreach ($item in @(Get-Scope)) {
            $acl = Get-Acl -LiteralPath $item.FullName
            $owner = Get-OwnerSid $acl
            if ($owner -ne $currentSid) { throw 'Snapshot found a non-current-user owner; no scope ACLs were modified' }
            $records += [ordered]@{ path = $item.FullName; directory = [bool]$item.PSIsContainer;
                bytes = if ($item.PSIsContainer) { 0 } else { $item.Length };
                last_write_ticks = $item.LastWriteTimeUtc.Ticks; creation_ticks = $item.CreationTimeUtc.Ticks;
                owner_sid = $owner; original_sddl = $acl.Sddl;
                sha256 = if ($item.PSIsContainer) { $null } else { Hash-Content $item.FullName } }
        }
        $manifest = [ordered]@{ schema_version = 1; created_at = [DateTime]::UtcNow.ToString('o'); workspace = $workspace;
            current_user_sid = $currentSid; allowed_sids = $allowedSids; relative_roots = $relativeRoots; records = $records;
            contents_in_report = $false; scope_acl_changes = 0 }
        $path = Join-Path $privateAudit 'original-manifest.json'
        if (Test-Path -LiteralPath $path) { throw 'Original manifest already exists and must not be replaced' }
        Write-PrivateJson $path $manifest
        @{ passed = $true; mode = $Mode; records = $records.Count; files = @($records | Where-Object { -not $_.directory }).Count;
            manifest_sha256 = Hash-Content $path; scope_acl_changes = 0 } | ConvertTo-Json -Compress
        exit 0
    }
    $manifest = Read-Manifest $privateAudit
    $scope = @(Get-Scope)
    if (($scope.FullName | ConvertTo-Json -Compress) -ne ($manifest.records.path | ConvertTo-Json -Compress)) { throw 'Exact frozen scope path list drifted' }
    foreach ($record in $manifest.records) {
        $item = Get-ExactItem $record.path
        $acl = Get-Acl -LiteralPath $record.path
        if ((Get-OwnerSid $acl) -ne $record.owner_sid -or [bool]$item.PSIsContainer -ne $record.directory) { throw 'Owner/type drifted' }
        if ($item.CreationTimeUtc.Ticks -ne $record.creation_ticks) { throw 'Creation identity metadata drifted' }
        if (-not $record.directory -and ($item.Length -ne $record.bytes -or $item.LastWriteTimeUtc.Ticks -ne $record.last_write_ticks)) { throw 'File metadata drifted' }
        if ($Mode -eq 'Apply' -and $acl.Sddl -ne $record.original_sddl) { throw 'DACL drifted before the first ACL modification' }
    }
    if ($Mode -eq 'Apply') {
        # Protect descendants before parents, after the complete preflight above.
        # This keeps intentional parent propagation distinct from external drift.
        foreach ($record in @($manifest.records | Sort-Object { $_.path.Length } -Descending)) { Set-PrivateAcl $record.path }
    }
    $verified = @()
    foreach ($record in $manifest.records) {
        $acl = Assert-PrivateAcl $record.path
        $actualHash = if ($record.directory) { $null } else { Hash-Content $record.path }
        if ($actualHash -ne $record.sha256) { throw 'Content hash changed during private ACL hardening' }
        $verified += [ordered]@{ path = $record.path; sha256 = $actualHash; owner_unchanged = $true;
            current_user_readable = $true; allowed_principals_only = $true; actual_sddl = $acl.Sddl }
    }
    $receipt = [ordered]@{ schema_version = 1; checked_at = [DateTime]::UtcNow.ToString('o'); mode = $Mode;
        original_manifest_sha256 = $ManifestHash; passed = $true; records = $verified;
        no_ownership_takeover_or_elevation = $true; no_docker_or_task_operation = $true; file_bytes_unchanged = $true }
    Write-PrivateJson (Join-Path $privateAudit ($Mode.ToLowerInvariant() + '-result.json')) $receipt
    @{ passed = $true; mode = $Mode; records = $verified.Count; files = @($manifest.records | Where-Object { -not $_.directory }).Count;
        content_hashes_unchanged = $true; allowed_principals = 3; current_user_readable = $true } | ConvertTo-Json -Compress
    exit 0
} catch {
    @{ passed = $false; mode = $Mode; error_type = $_.Exception.GetType().Name; reason = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
