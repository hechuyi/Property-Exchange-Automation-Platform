[CmdletBinding()]
param(
    [string]$DataRoot = "",
    [string]$ProjectRoot = "",
    [string]$RuntimeConfigFile = ""
)

$ErrorActionPreference = "Stop"

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Root([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return [System.IO.Path]::GetFullPath((Join-Path $script:ScriptRoot ".."))
    }
    return [System.IO.Path]::GetFullPath($value)
}

function Ensure-Dir([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) {
        return
    }
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

function Resolve-ConfiguredPath([string]$baseDir, [string]$rawPath) {
    if ([string]::IsNullOrWhiteSpace($rawPath)) {
        throw "path value is empty"
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($rawPath.Trim())
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $baseDir $expanded))
}

function Get-RuntimeConfig([string]$projectAbs, [string]$runtimeConfigOverride) {
    $runtimeConfig = $runtimeConfigOverride
    if ([string]::IsNullOrWhiteSpace($runtimeConfig)) {
        $runtimeConfig = [Environment]::GetEnvironmentVariable("PEAP_RUNTIME_CONFIG_FILE")
    }
    if ([string]::IsNullOrWhiteSpace($runtimeConfig)) {
        $runtimeConfig = Join-Path $projectAbs "assets\runtime_config.json"
    }
    $runtimeConfig = [System.IO.Path]::GetFullPath($runtimeConfig)
    if (-not (Test-Path -LiteralPath $runtimeConfig)) {
        throw "runtime config file not found: $runtimeConfig"
    }

    $payload = Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $payload -or $null -eq $payload.PSObject.Properties['paths']) {
        throw "invalid runtime config: missing paths object ($runtimeConfig)"
    }
    return $payload
}

$projectAbs = Resolve-Root $ProjectRoot
$runtimeConfig = Get-RuntimeConfig $projectAbs $RuntimeConfigFile
$paths = $runtimeConfig.paths
if ($null -eq $paths -or $null -eq $paths.PSObject.Properties['data_root']) {
    throw "invalid runtime config: missing paths.data_root"
}

if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $dataAbs = Resolve-ConfiguredPath $projectAbs ([string]$paths.data_root)
}
else {
    $dataAbs = Resolve-Root $DataRoot
}

Write-Host "project_root = $projectAbs"
Write-Host "data_root    = $dataAbs"

$dirs = [System.Collections.Generic.List[string]]::new()
$dirs.Add($dataAbs)
$dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.html_folder)))
$dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.auto_html_folder)))
$dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.log_dir)))
$dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.output_excel_dir)))

$regressionRoot = Resolve-ConfiguredPath $dataAbs ([string]$paths.regression_root)
$dirs.Add($regressionRoot)
$dirs.Add((Join-Path $regressionRoot "RawPages"))

if ($paths.PSObject.Properties['regression_workdir_root']) {
    $dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.regression_workdir_root)))
}
if ($paths.PSObject.Properties['compare_report_dir']) {
    $dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.compare_report_dir)))
}
if ($paths.PSObject.Properties['download_chunk_state_dir']) {
    $dirs.Add((Resolve-ConfiguredPath $dataAbs ([string]$paths.download_chunk_state_dir)))
}
if ($paths.PSObject.Properties['parser_cache_db']) {
    $parserCachePath = Resolve-ConfiguredPath $dataAbs ([string]$paths.parser_cache_db)
    $dirs.Add((Split-Path -Parent $parserCachePath))
}

$dirs.Add((Join-Path $dataAbs "outputs\submission"))
$dirs.Add((Join-Path $dataAbs "outputs\postprocess"))
$dirs.Add((Join-Path $dataAbs "outputs\postprocess_audit"))
$dirs.Add((Join-Path $dataAbs "backup"))

foreach ($dir in ($dirs | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)) {
    Ensure-Dir $dir
}

Write-Host "Done. Verify runtime config:"
Write-Host "  paths.data_root = `"$dataAbs`""
Write-Host "If runtime config is outside repo, set PEAP_RUNTIME_CONFIG_FILE or pass -RuntimeConfigFile."
