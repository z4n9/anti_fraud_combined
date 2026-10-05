param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$sharedPython = Join-Path (Split-Path $PSScriptRoot -Parent) 'test_env\Scripts\python.exe'
$pythonPath = if (Test-Path -LiteralPath $localPython) { $localPython } elseif (Test-Path -LiteralPath $sharedPython) { $sharedPython } else { $null }
if (-not $pythonPath) {
    throw 'Создайте .venv и установите requirements.txt по инструкции README.md.'
}

$backendPath = Join-Path $PSScriptRoot 'backend'
$arguments = @('-m', 'uvicorn', 'app.main:app', '--app-dir', $backendPath, '--host', '127.0.0.1', '--port', "$Port")
if ($Reload) { $arguments += '--reload' }
& $pythonPath @arguments
exit $LASTEXITCODE
