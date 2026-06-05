param(
    [string]$SecretPath = ''
)

$ErrorActionPreference = 'Stop'

if (-not $SecretPath) {
    $SecretPath = Join-Path $env:USERPROFILE '.codex\.sandbox-secrets\mem0-codex-mcp.env'
}
if (-not [System.IO.Path]::IsPathRooted($SecretPath)) {
    $SecretPath = Join-Path $PWD $SecretPath
}

$secretPath = $SecretPath
if (-not (Test-Path -LiteralPath $secretPath)) {
    Write-Error "Missing Mem0 MCP secret file: $secretPath"
    exit 1
}

Get-Content -LiteralPath $secretPath | ForEach-Object {
    if (-not $_ -or $_.TrimStart().StartsWith('#')) { return }
    $parts = $_ -split '=', 2
    if ($parts.Count -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
    }
}

python (Join-Path $PSScriptRoot 'mem0_mcp_server.py')
