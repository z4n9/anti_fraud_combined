param([string[]]$TestPath = @('backend/tests'))
$ErrorActionPreference = 'Stop'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$sharedPython = Join-Path (Split-Path $PSScriptRoot -Parent) 'test_env\Scripts\python.exe'
$pythonPath = if (Test-Path -LiteralPath $localPython) { $localPython } elseif (Test-Path -LiteralPath $sharedPython) { $sharedPython } else { $null }
if (-not $pythonPath) {
    throw 'Создайте .venv и установите requirements.txt по инструкции README.md.'
}

Push-Location -LiteralPath $PSScriptRoot
try {
    # A unique workspace-local temp directory avoids inaccessible system pytest caches.
    $testTempRoot = Join-Path $PSScriptRoot '.test-tmp'
    [void][System.IO.Directory]::CreateDirectory($testTempRoot)
    $testTempPath = Join-Path $testTempRoot ([guid]::NewGuid().ToString('N'))
    & $pythonPath -m pytest -c backend/pyproject.toml @TestPath -q -p no:cacheprovider --basetemp $testTempPath
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $testExitCode
