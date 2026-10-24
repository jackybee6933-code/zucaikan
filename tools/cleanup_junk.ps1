param(
    [switch]$Apply,
    [switch]$IncludeOldPackages
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$targets = [System.Collections.Generic.List[string]]::new()

function Add-Target([string]$relativePath) {
    $path = [IO.Path]::GetFullPath((Join-Path $root $relativePath))
    if (-not $path.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝处理工作区外路径：$path"
    }
    if ((Test-Path -LiteralPath $path) -and -not $targets.Contains($path)) {
        $targets.Add($path)
    }
}

# 构建缓存与临时排障文件；不触碰 data/cache/reports。
@(
    "build",
    "__pycache__",
    "football_analysis/__pycache__",
    "tests/__pycache__",
    "tools/__pycache__",
    ".tmp_upd",
    ".tmp_check.py",
    ".tmp_dl.zip",
    ".tmp_find_end.py",
    ".last_run.log",
    "tools/check_bets.py",
    "tools/inspect_26146.py",
    "dist/football_analysis_full.zip",
    "dist/zucikan1.3.7.zip"
) | ForEach-Object { Add-Target $_ }

Get-ChildItem -LiteralPath (Join-Path $root "dist") -Directory -Force |
    Where-Object { $_.Name -match "测试_v1\.3$" } |
    ForEach-Object { Add-Target ([IO.Path]::GetRelativePath($root, $_.FullName)) }

if ($IncludeOldPackages) {
    Get-ChildItem -LiteralPath (Join-Path $root "dist") -File -Filter "*.zip" |
        Where-Object { $_.Name -match "202610(14|16|17|18|19|20|21|22)\.zip$" } |
        ForEach-Object { Add-Target ([IO.Path]::GetRelativePath($root, $_.FullName)) }
}

if ($targets.Count -eq 0) {
    Write-Output "没有找到待清理项。"
    exit 0
}

if (-not $Apply) {
    Write-Output "预览模式，以下项目未删除。确认后加 -Apply 执行："
    $targets | ForEach-Object { Write-Output $_ }
    exit 0
}

foreach ($path in $targets) {
    Remove-Item -LiteralPath $path -Recurse -Force
    Write-Output "已删除：$path"
}
