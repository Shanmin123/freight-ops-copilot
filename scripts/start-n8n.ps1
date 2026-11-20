[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 5678,
    [ValidateRange(1, 65535)][int]$BackendPort = 8000,
    [string]$Node24Path = '',
    [switch]$ImportWorkflow
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $projectRoot 'backend/var/n8n-runtime'
$data = Join-Path $projectRoot "backend/var/n8n-data-$Port"
$version = '2.48.6'

if (-not $Node24Path) {
    $nvmRoot = if ($env:NVM_HOME) { $env:NVM_HOME } else { Join-Path $env:LOCALAPPDATA 'nvm' }
    $Node24Path = Get-ChildItem -LiteralPath $nvmRoot -Directory -Filter 'v24.*' -ErrorAction SilentlyContinue |
        Sort-Object { [version]$_.Name.TrimStart('v') } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Node24Path) {
    throw '请安装 Node.js 24，或用 -Node24Path 指定 Node 24 的安装目录。'
}
$node = Join-Path $Node24Path 'node.exe'
$npm = Join-Path $Node24Path 'npm.cmd'
if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $npm)) {
    throw 'Node 24 目录中找不到 node.exe 或 npm.cmd。'
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with code $LASTEXITCODE." }
}

$priorEnvironment = @{}
$processSettings = @{
    'Path' = "$Node24Path;$env:Path"
    'N8N_USER_FOLDER' = $data
    'N8N_PORT' = [string]$Port
    'N8N_LISTEN_ADDRESS' = '127.0.0.1'
    'N8N_HOST' = '127.0.0.1'
    'N8N_PROTOCOL' = 'http'
    'N8N_WEBHOOK_URL' = "http://127.0.0.1:$Port/"
    'N8N_DIAGNOSTICS_ENABLED' = 'false'
    'N8N_VERSION_NOTIFICATIONS_ENABLED' = 'false'
    'N8N_TEMPLATES_ENABLED' = 'false'
    'N8N_SECURE_COOKIE' = 'false'
    'N8N_LOG_LEVEL' = 'warn'
}
try {
    foreach ($entry in $processSettings.GetEnumerator()) {
        $priorEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
    $actualVersion = & $node --version
    if ($LASTEXITCODE -ne 0 -or $actualVersion -notmatch '^v24\.') {
        throw '启动 n8n 需要 Node.js 24。'
    }

    New-Item -ItemType Directory -Force $runtime, $data | Out-Null
    $manifest = Join-Path $runtime 'node_modules/n8n/package.json'
    $installed = if (Test-Path -LiteralPath $manifest) {
        (Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json).version
    }
    $cli = Join-Path $runtime 'node_modules/n8n/bin/n8n'
    if ($installed -ne $version -or -not (Test-Path -LiteralPath $cli) -or
        -not (Test-Path -LiteralPath (Join-Path $runtime 'package-lock.json'))) {
        Invoke-Checked $npm @('install', '--prefix', $runtime, '--no-audit', '--no-fund', '--save-exact',
            '--prefer-offline', '--fetch-retries=4', '--maxsockets=10', "n8n@$version")
    }

    $marker = Join-Path $data 'forwarder-mail-port.txt'
    $previousPort = if (Test-Path -LiteralPath $marker) {
        (Get-Content -LiteralPath $marker -Raw).Trim()
    }
    if ($ImportWorkflow -or -not $previousPort) {
        $original = Join-Path $projectRoot 'workflows/forwarder-mail.json'
        $workflowPath = Join-Path $data 'forwarder-mail.json'
        $workflow = [IO.File]::ReadAllText($original).Replace(
            'http://127.0.0.1:8000/api/automation/', "http://127.0.0.1:$BackendPort/api/automation/"
        )
        [IO.File]::WriteAllText($workflowPath, $workflow)
        Invoke-Checked $node @($cli, 'import:workflow', "--input=$workflowPath")
        Invoke-Checked $node @($cli, 'publish:workflow', '--id=ForwarderMailP6X')
        [IO.File]::WriteAllText($marker, [string]$BackendPort)
    } elseif ($previousPort -ne [string]$BackendPort) {
        throw '后端端口已变化。请加 -ImportWorkflow 重新导入此演示流程。'
    }
    Write-Host "n8n http://127.0.0.1:$Port"
    Write-Host "货代客服自动化 http://127.0.0.1:$BackendPort/automation"
    Invoke-Checked $node @($cli, 'start')
} finally {
    foreach ($entry in $priorEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
}
