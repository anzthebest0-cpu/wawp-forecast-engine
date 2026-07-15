$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$vault = 'D:\BMKG_KNOWLEDGE\20 Projects\WAWP'
$graph = Join-Path $repo 'graphify-out\graph.json'

Write-Output "WAWP repository: $repo"
Write-Output ("Knowledge vault: " + $(if (Test-Path -LiteralPath $vault) { 'available' } else { 'missing' }))
Write-Output ("Graphify graph: " + $(if (Test-Path -LiteralPath $graph) { 'available' } else { 'not built' }))

if (Test-Path -LiteralPath $graph) {
    $graphTime = (Get-Item -LiteralPath $graph).LastWriteTimeUtc
    $latestSource = Get-ChildItem -LiteralPath $repo -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\(graphify-out|docs\\data|\.git|node_modules)\\' } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($latestSource -and $latestSource.LastWriteTimeUtc -gt $graphTime) {
        Write-Warning "Graphify graph predates maintained source changes; refresh before dependency analysis."
    }
}
