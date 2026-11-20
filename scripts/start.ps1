[CmdletBinding()]
param(
    [switch]$Rebuild,
    [ValidateRange(1, 65535)][int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program exited with code $LASTEXITCODE."
    }
}

Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        Invoke-Checked python @('-m', 'venv', 'backend/.venv')
    }
    Invoke-Checked $pythonPath @('-m', 'pip', 'install', '-q', '-r', 'backend/requirements.txt')

    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules') -or $Rebuild) {
            Invoke-Checked npm.cmd @('ci', '--no-audit', '--no-fund')
        }
        if (-not (Test-Path -LiteralPath 'dist/index.html') -or $Rebuild) {
            Invoke-Checked npm.cmd @('run', 'build')
        }
    } finally {
        Pop-Location
    }

    Write-Host "工作台 http://127.0.0.1:$Port/workbench"
    Write-Host "客户聊天窗口 http://127.0.0.1:$Port/chat"
    Invoke-Checked $pythonPath @('-m', 'uvicorn', 'forwarder.main:create_app', '--factory',
        '--app-dir', 'backend', '--host', '127.0.0.1', '--port', "$Port")
} finally {
    Pop-Location
}
