param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    throw "Inno Setup compiler iscc was not found."
}

if (-not $Version) {
    $init = Get-Content (Join-Path $root "football_analysis\__init__.py") -Raw -Encoding UTF8
    $match = [regex]::Match($init, '__version__\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw "Could not read application version."
    }
    $Version = $match.Groups[1].Value
}

& $iscc.Source "/DAppVersion=$Version" (Join-Path $root "football_analysis.iss")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Write-Output "dist\football_analysis_setup.exe"
