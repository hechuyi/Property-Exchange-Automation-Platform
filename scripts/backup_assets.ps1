param(
    [string]$SourceRoot = "",
    [string]$BackupRoot = "",
    [string]$RuntimeConfigFile = "",
    [string]$Label = "",
    [ValidateSet(0, 1)]
    [int]$UseTimestampSubdir = 0,
    [ValidateSet(0, 1)]
    [int]$IncludeReference = 1,
    [ValidateSet(0, 1)]
    [int]$IncludePostprocessOutput = 1,
    [ValidateSet(0, 1)]
    [int]$IncludePpeConfig = 1,
    [ValidateSet(0, 1)]
    [int]$IncludeRootExcel = 1,
    [ValidateSet(0, 1)]
    [int]$IncludeWebManual = 0,
    [ValidateSet(0, 1)]
    [int]$IncludeWebAuto = 0,
    [ValidateSet(0, 1)]
    [int]$IncludeLogs = 0,
    [ValidateSet(0, 1)]
    [int]$ExcludeLockFiles = 1,
    [ValidateSet(0, 1)]
    [int]$GenerateHashManifest = 1,
    [ValidateSet(0, 1)]
    [int]$QuietRobocopyLog = 1,
    [ValidateSet("SHA256", "SHA1", "MD5")]
    [string]$HashAlgorithm = "SHA256",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function As-Bool {
    param([int]$Value)
    return ($Value -eq 1)
}

function New-UnicodeString {
    param([int[]]$CodePoints)
    return -join ($CodePoints | ForEach-Object { [char]$_ })
}

$manualHtmlDirName = New-UnicodeString -CodePoints @(0x7F51, 0x9875, 0x6682, 0x5B58)
$autoHtmlDirName = New-UnicodeString -CodePoints @(0x7F51, 0x9875, 0x6682, 0x5B58, 0x005F, 0x81EA, 0x52A8)

function Resolve-AbsPath {
    param([string]$PathValue)
    return [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PathValue).Path)
}

function Resolve-DefaultSourceRoot {
    param([string]$RuntimeConfigOverride)
    $scriptDir = Split-Path -Parent $PSCommandPath
    $projectDir = [System.IO.Path]::GetFullPath((Join-Path $scriptDir ".."))
    $runtimeConfig = $RuntimeConfigOverride
    if ([string]::IsNullOrWhiteSpace($runtimeConfig)) {
        $runtimeConfig = [Environment]::GetEnvironmentVariable("PEAP_RUNTIME_CONFIG_FILE")
    }
    if ([string]::IsNullOrWhiteSpace($runtimeConfig)) {
        $runtimeConfig = Join-Path $projectDir "assets\runtime_config.json"
    }
    $runtimeConfig = [System.IO.Path]::GetFullPath($runtimeConfig)
    if (-not (Test-Path -LiteralPath $runtimeConfig)) {
        throw "runtime config file not found: $runtimeConfig"
    }

    $payload = Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $payload -or $null -eq $payload.PSObject.Properties['paths']) {
        throw "invalid runtime config: missing paths object ($runtimeConfig)"
    }
    $paths = $payload.paths
    if ($null -eq $paths -or $null -eq $paths.PSObject.Properties['data_root']) {
        throw "invalid runtime config: missing paths.data_root ($runtimeConfig)"
    }

    $dataRootRaw = [string]$paths.data_root
    if ([string]::IsNullOrWhiteSpace($dataRootRaw)) {
        throw "invalid runtime config: paths.data_root is empty ($runtimeConfig)"
    }
    if ([System.IO.Path]::IsPathRooted($dataRootRaw)) {
        return [System.IO.Path]::GetFullPath($dataRootRaw)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectDir $dataRootRaw))
}

function Ensure-Dir {
    param([string]$PathValue)
    if (-not (Test-Path -LiteralPath $PathValue)) {
        New-Item -ItemType Directory -Path $PathValue | Out-Null
    }
}

function Invoke-RobocopySafe {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeFiles = @()
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Host "[SKIP] not found: $Source"
        return
    }
    Ensure-Dir -PathValue $Destination
    $args = @(
        $Source,
        $Destination,
        "/E",
        "/R:1",
        "/W:1"
    )
    if (As-Bool $QuietRobocopyLog) {
        # Keep summary only; avoid per-file/per-dir output explosion.
        $args += @("/NFL", "/NDL", "/NP")
    }
    if ($ExcludeFiles.Count -gt 0) {
        $args += "/XF"
        $args += $ExcludeFiles
    }
    if ($DryRun.IsPresent) {
        $args += "/L"
    }

    & robocopy @args | Out-Host
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "robocopy failed: source=$Source destination=$Destination exit_code=$code"
    }
}

function Copy-RootFilesByPattern {
    param(
        [string]$SourceDir,
        [string]$DestinationDir,
        [string]$Pattern
    )
    Ensure-Dir -PathValue $DestinationDir
    $files = Get-ChildItem -LiteralPath $SourceDir -File -Filter $Pattern -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        if ($DryRun.IsPresent) {
            Write-Host "[DRYRUN] copy $($f.FullName) -> $DestinationDir"
        }
        else {
            Copy-Item -LiteralPath $f.FullName -Destination $DestinationDir -Force
        }
    }
}

$sourceArg = $SourceRoot
if ([string]::IsNullOrWhiteSpace($sourceArg)) {
    $sourceArg = Resolve-DefaultSourceRoot -RuntimeConfigOverride $RuntimeConfigFile
}
$sourceAbs = Resolve-AbsPath -PathValue $sourceArg
$resolvedBackupRoot = $BackupRoot
if ([string]::IsNullOrWhiteSpace($resolvedBackupRoot)) {
    $resolvedBackupRoot = Join-Path $sourceAbs "backup"
}
$resolvedBackupRoot = [System.IO.Path]::GetFullPath($resolvedBackupRoot)

$destAbs = ""
if (As-Bool $UseTimestampSubdir) {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        $Label = Get-Date -Format "yyyyMMdd_HHmmss"
    }
    $destAbs = [System.IO.Path]::GetFullPath((Join-Path $resolvedBackupRoot $Label))
}
else {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        $destAbs = [System.IO.Path]::GetFullPath($resolvedBackupRoot)
    }
    else {
        $destAbs = [System.IO.Path]::GetFullPath((Join-Path $resolvedBackupRoot $Label))
    }
}

Ensure-Dir -PathValue $destAbs

$lockPatterns = @()
if (As-Bool $ExcludeLockFiles) {
    $lockPatterns = @(".~lock*")
}

Write-Host "=== Backup Plan ==="
Write-Host "source: $sourceAbs"
Write-Host "dest  : $destAbs"
Write-Host "dryrun: $($DryRun.IsPresent)"
Write-Host "use_timestamp_subdir     = $(As-Bool $UseTimestampSubdir)"
Write-Host "quiet_robocopy_log       = $(As-Bool $QuietRobocopyLog)"
Write-Host "include_reference        = $(As-Bool $IncludeReference)"
Write-Host "include_postprocess_out  = $(As-Bool $IncludePostprocessOutput)"
Write-Host "include_ppe_config       = $(As-Bool $IncludePpeConfig)"
Write-Host "include_root_excel       = $(As-Bool $IncludeRootExcel)"
Write-Host "include_web_manual       = $(As-Bool $IncludeWebManual)"
Write-Host "include_web_auto         = $(As-Bool $IncludeWebAuto)"
Write-Host "include_logs             = $(As-Bool $IncludeLogs)"
Write-Host "exclude_lock_files       = $(As-Bool $ExcludeLockFiles)"
Write-Host "generate_hash_manifest   = $(As-Bool $GenerateHashManifest)"
Write-Host "hash_algorithm           = $HashAlgorithm"
Write-Host "==================="

if (As-Bool $IncludeReference) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs "reference") `
        -Destination (Join-Path $destAbs "reference")
}

if (As-Bool $IncludePostprocessOutput) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs "peap_postprocess\postprocess_output") `
        -Destination (Join-Path $destAbs "postprocess_output") `
        -ExcludeFiles $lockPatterns
}

if (As-Bool $IncludePpeConfig) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs "peap_postprocess\ppe_config") `
        -Destination (Join-Path $destAbs "ppe_config") `
        -ExcludeFiles $lockPatterns
}

if (As-Bool $IncludeWebManual) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs $manualHtmlDirName) `
        -Destination (Join-Path $destAbs $manualHtmlDirName)
}

if (As-Bool $IncludeWebAuto) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs $autoHtmlDirName) `
        -Destination (Join-Path $destAbs $autoHtmlDirName)
}

if (As-Bool $IncludeLogs) {
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs "logs") `
        -Destination (Join-Path $destAbs "logs") `
        -ExcludeFiles $lockPatterns
    Invoke-RobocopySafe `
        -Source (Join-Path $sourceAbs "peap_postprocess\logs") `
        -Destination (Join-Path $destAbs "peap_postprocess_logs") `
        -ExcludeFiles $lockPatterns
}

if (As-Bool $IncludeRootExcel) {
    Copy-RootFilesByPattern `
        -SourceDir $sourceAbs `
        -DestinationDir (Join-Path $destAbs "excel_root") `
        -Pattern "*.xlsx"
}

$meta = [ordered]@{
    generated_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    source_root = $sourceAbs
    backup_root = $resolvedBackupRoot
    backup_path = $destAbs
    label = $Label
    use_timestamp_subdir = (As-Bool $UseTimestampSubdir)
    dry_run = $DryRun.IsPresent
    include_reference = (As-Bool $IncludeReference)
    include_postprocess_output = (As-Bool $IncludePostprocessOutput)
    include_ppe_config = (As-Bool $IncludePpeConfig)
    include_root_excel = (As-Bool $IncludeRootExcel)
    include_web_manual = (As-Bool $IncludeWebManual)
    include_web_auto = (As-Bool $IncludeWebAuto)
    include_logs = (As-Bool $IncludeLogs)
    exclude_lock_files = (As-Bool $ExcludeLockFiles)
    generate_hash_manifest = (As-Bool $GenerateHashManifest)
    quiet_robocopy_log = (As-Bool $QuietRobocopyLog)
    hash_algorithm = $HashAlgorithm
}

if (-not $DryRun.IsPresent) {
    $meta | ConvertTo-Json -Depth 4 | Out-File -LiteralPath (Join-Path $destAbs "backup_meta.json") -Encoding UTF8
}

if ((As-Bool $GenerateHashManifest) -and -not $DryRun.IsPresent) {
    Get-ChildItem -LiteralPath $destAbs -Recurse -File |
        Get-FileHash -Algorithm $HashAlgorithm |
        Select-Object Algorithm, Hash, Path |
        Export-Csv -NoTypeInformation -Encoding UTF8 -LiteralPath (Join-Path $destAbs "hash_manifest.csv")
}

Write-Host ""
Write-Host "Backup completed."
Write-Host "Backup path: $destAbs"
