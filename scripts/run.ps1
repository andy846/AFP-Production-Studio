<#
.SYNOPSIS
    Starts AFP Production Studio on this machine.

.DESCRIPTION
    Creates a virtual environment if there is not one, installs the
    Python dependencies and starts the local web server.

    The server binds to 127.0.0.1 by default. Nothing is sent anywhere.

.PARAMETER Port
    The port to listen on (default: 8787).

.PARAMETER Host
    The interface to bind to (default: 127.0.0.1).

.PARAMETER Reload
    Restart the server when a source file changes.
#>

[CmdletBinding()]
param(
    [int]$Port = 8787,
    [string]$Host = "127.0.0.1",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $RepoRoot ".venv"

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Write-Host "Creating the virtual environment..." -ForegroundColor Cyan
    python -m venv $Venv
}

$Python = Join-Path $Venv "Scripts\python.exe"
Write-Host "Installing the Python dependencies..." -ForegroundColor Cyan
& $Python -m pip install --disable-pip-version-check -q `
    -r (Join-Path $RepoRoot "backend\requirements.txt")

$env:AFP_STUDIO_HOST = $Host
$env:AFP_STUDIO_PORT = "$Port"
$env:PYTHONPATH = "$RepoRoot\backend;$RepoRoot\backend\vendor\readafp\src"

Write-Host "AFP Production Studio is starting on http://${Host}:${Port}" -ForegroundColor Green
if ($Reload) {
    & $Python -m afpstudio
} else {
    & $Python -m afpstudio
}
