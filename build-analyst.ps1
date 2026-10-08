$ErrorActionPreference = 'Stop'
$frontendPath = Join-Path $PSScriptRoot 'frontend\analyst'
if (-not (Test-Path -LiteralPath (Join-Path $frontendPath 'node_modules'))) {
    throw 'Install frontend dependencies first: npm.cmd ci --prefix frontend/analyst'
}
& npm.cmd --prefix $frontendPath run build
exit $LASTEXITCODE
