param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^v\d+\.\d+(?:\.\d+)?$")]
    [string]$Tag,

    [string]$Remote = "origin",
    [string]$Branch = "master",
    [ValidateRange(1, 5)]
    [int]$MaxAttempts = 5
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git was not found."
}

$gitArgs = @("push", $Remote, $Branch, "refs/tags/$Tag")
$fatalPatterns = @(
    "authentication failed",
    "permission denied",
    "non-fast-forward",
    "protected branch",
    "rejected"
)

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-Host "Pushing $Branch and $Tag (attempt $attempt/$MaxAttempts)..."
    $output = & git @gitArgs 2>&1
    $exitCode = $LASTEXITCODE
    $output | ForEach-Object { Write-Host $_ }

    if ($exitCode -eq 0) {
        Write-Host "Push completed."
        exit 0
    }

    $text = ($output | Out-String).ToLowerInvariant()
    if ($fatalPatterns | Where-Object { $text.Contains($_) }) {
        throw "Git push failed with a non-transient error."
    }
    if ($attempt -eq $MaxAttempts) {
        throw "Git push failed after $MaxAttempts attempts."
    }

    $delay = [int][math]::Pow(2, $attempt - 1) * 15
    Write-Host "Transient push failure; retrying in $delay seconds."
    Start-Sleep -Seconds $delay
}
