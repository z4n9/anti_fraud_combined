param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
function Assert-RealProjectPath([string]$path) {
    $absolute = [System.IO.Path]::GetFullPath($path).TrimEnd('\')
    if ($absolute -ne $projectRoot -and -not $absolute.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target escaped the project: $absolute"
    }
    $probe = $absolute
    while ($probe.Length -ge $projectRoot.Length) {
        if (Test-Path -LiteralPath $probe) {
            $entry = Get-Item -LiteralPath $probe -Force
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Cleanup refuses a linked target or ancestor: $probe"
            }
        }
        if ($probe -eq $projectRoot) { break }
        $probe = [System.IO.Path]::GetDirectoryName($probe)
    }
}
Assert-RealProjectPath $projectRoot
Assert-RealProjectPath (Join-Path $projectRoot 'backend')
$allowed = @('.test-tmp', '.qa-security-tmp', '.pytest_cache', 'backend\.pytest_cache')
$candidates = @($allowed | ForEach-Object { Join-Path $projectRoot $_ })
$candidates += @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'backend') -Directory -Force |
    Where-Object { $_.Name -like 'pytest-cache-files-*' } | ForEach-Object { $_.FullName })
$candidates += @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'backend') -Directory -Recurse -Force |
    Where-Object { $_.Name -eq '__pycache__' } | ForEach-Object { $_.FullName })
$removed = 0
foreach ($candidate in ($candidates | Sort-Object -Unique)) {
    $resolved = [System.IO.Path]::GetFullPath($candidate)
    if (-not $resolved.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target escaped the project: $resolved"
    }
    Assert-RealProjectPath $resolved
    if (-not (Test-Path -LiteralPath $resolved)) { continue }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Cleanup refuses a link: $resolved"
    }
    $linkedChild = Get-ChildItem -LiteralPath $resolved -Recurse -Force |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($linkedChild) { throw "Cleanup refuses a directory containing a link: $resolved" }
    if ($Apply) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
        $removed++
        Write-Output "Removed generated cache: $resolved"
    } else {
        Write-Output "Preview generated cache: $resolved"
    }
}
if ($Apply) { Write-Output "Removed $removed generated cache directories." }
else { Write-Output 'Preview only. Stop tests first; use -Apply to remove these caches.' }
