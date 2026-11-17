param(
    [ValidateSet("Prepare", "Start", "Stop")]
    [string]$Action = "Start",
    [int]$Port = 8766,
    [string]$SourceInstaller = "",
    [string]$LegacyDist = "",
    [switch]$LaunchQuery
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildRoot = Join-Path $root "build"
$testRoot = Join-Path $buildRoot "local-upgrade-v18"
$serverRoot = Join-Path $testRoot "server"
$sandboxRoot = Join-Path $testRoot "sandbox"
$sandboxInstall = Join-Path $sandboxRoot "FootballAnalysis"
$logPath = Join-Path $serverRoot "local-update-test.log"
$pidPath = Join-Path $serverRoot "http-server.pid"
$serverAccessLog = Join-Path $serverRoot "http-server-access.log"
$serverOutputLog = Join-Path $serverRoot "http-server-output.log"

function Write-Phase([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $buildRoot -PathType Container)) {
    throw "Expected build directory not found: $buildRoot"
}

if ($Action -eq "Stop") {
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        Write-Host "Local update server is not recorded as running."
        exit 0
    }
    $serverPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
    if ($process -and $process.CommandLine -like "*http.server $Port*" -and
        $process.CommandLine -like "*$serverRoot*") {
        Stop-Process -Id $serverPid
        Write-Phase "Stopped local HTTP server (PID $serverPid)."
    } else {
        Write-Phase "Recorded server process is already stopped or no longer matches; left it untouched."
    }
    Remove-Item -LiteralPath $pidPath -Force
    exit 0
}

if (-not $SourceInstaller) {
    $SourceInstaller = Join-Path $testRoot "dist\football_analysis_setup.exe"
}
if (-not (Test-Path -LiteralPath $SourceInstaller -PathType Leaf)) {
    throw "Installer not found: $SourceInstaller"
}

New-Item -ItemType Directory -Path $serverRoot -Force | Out-Null
$resolvedSourceInstaller = (Resolve-Path -LiteralPath $SourceInstaller).Path
$installerPath = Join-Path $serverRoot "football_analysis_setup.exe"
Write-Phase "Staging installer from $resolvedSourceInstaller (source is read-only)."
Copy-Item -LiteralPath $resolvedSourceInstaller -Destination $installerPath -Force
$sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    version = "1.8"
    release_url = "http://127.0.0.1:$Port/local-test"
    notes = "LOCAL TEST ONLY: isolated upgrade verification."
    installer_url = "http://127.0.0.1:$Port/football_analysis_setup.exe"
    installer_sha256 = $sha256
    installer_package_type = "inno-setup"
}
$manifestPath = Join-Path $serverRoot "latest.json"
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText(
    $manifestPath,
    $manifestJson + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)
Write-Phase "Wrote isolated manifest: $manifestPath (version 1.8, SHA-256 $sha256)."

if ($Action -eq "Prepare") {
    if (-not $LegacyDist) {
        $LegacyDist = Join-Path $buildRoot "release-v1.7\dist"
    }
    $legacyRoot = (Resolve-Path -LiteralPath $LegacyDist).Path
    foreach ($name in @("football_analysis.exe", "updater.exe")) {
        $source = Join-Path $legacyRoot $name
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Legacy v1.7 file not found: $source"
        }
    }
    if (-not $legacyRoot.StartsWith(
        $buildRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "LegacyDist must be inside build/: $legacyRoot"
    }
    New-Item -ItemType Directory -Path $sandboxInstall -Force | Out-Null
    foreach ($name in @("football_analysis.exe", "updater.exe")) {
        Copy-Item -LiteralPath (Join-Path $legacyRoot $name) `
            -Destination (Join-Path $sandboxInstall $name) -Force
    }
    Copy-Item -LiteralPath (Join-Path $root "tools\scrape_odds_pages.py") `
        -Destination (Join-Path $sandboxInstall "scrape_odds_pages.py") -Force
    foreach ($name in @("cache", "data", "reports", "tools")) {
        New-Item -ItemType Directory -Path (Join-Path $sandboxInstall $name) -Force | Out-Null
    }
    Write-Phase "Prepared isolated v1.7 install sandbox: $sandboxInstall"
    Write-Phase "Staged v1.8 installer and local update manifest under: $serverRoot"
    Write-Host "Sandbox is ready. Start the local server and old query page with:"
    Write-Host ".\tools\local_update_test.ps1 -Action Start -LaunchQuery"
    Write-Host "The update page URL is http://127.0.0.1:$Port/latest.json"
    exit 0
}

$existingPid = 0
if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath -Raw)
}
$existing = if ($existingPid) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $existingPid"
} else {
    $null
}
if ($existing -and $existing.CommandLine -like "*http.server $Port*" -and
    $existing.CommandLine -like "*$serverRoot*") {
    Write-Phase "Reusing local HTTP server (PID $existingPid)."
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
    $process = Start-Process -FilePath $python `
        -ArgumentList @("-m", "http.server", "$Port", "--bind", "127.0.0.1", "--directory", $serverRoot) `
        -WorkingDirectory $serverRoot `
        -RedirectStandardOutput $serverOutputLog `
        -RedirectStandardError $serverAccessLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
    Write-Phase "Started loopback HTTP server at http://127.0.0.1:$Port/ (PID $($process.Id))."
}

$manifestUrl = "http://127.0.0.1:$Port/latest.json"
Write-Phase "Update manifest URL: $manifestUrl"
Write-Phase "HTTP access log: $serverAccessLog"

if ($LaunchQuery) {
    $env:FA_UPDATE_MANIFEST_URL = $manifestUrl
    $launchExe = Join-Path $sandboxInstall "football_analysis.exe"
    $dbPath = Join-Path $sandboxInstall "data\football_analysis.sqlite"
    $reportsPath = Join-Path $sandboxInstall "reports"
    $arguments = @(
        "query",
        "--serve",
        "--db-path", "`"$dbPath`"",
        "--output-dir", "`"$reportsPath`"",
        "--port", "8765"
    )
    $previousManifestUrl = $env:FA_UPDATE_MANIFEST_URL
    try {
        $env:FA_UPDATE_MANIFEST_URL = $manifestUrl
        $launched = Start-Process -FilePath $launchExe `
            -ArgumentList $arguments `
            -WorkingDirectory $sandboxInstall `
            -PassThru
    } finally {
        if ($null -eq $previousManifestUrl) {
            Remove-Item Env:FA_UPDATE_MANIFEST_URL -ErrorAction SilentlyContinue
        } else {
            $env:FA_UPDATE_MANIFEST_URL = $previousManifestUrl
        }
    }
    Write-Phase "Launched sandbox v1.7 query page (PID $($launched.Id)); process-scoped update URL is $manifestUrl."
}
Write-Host "Stop server with: .\tools\local_update_test.ps1 -Action Stop -Port $Port"
