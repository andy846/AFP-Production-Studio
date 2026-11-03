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

.PARAMETER Desktop
    Open the application in a native window instead of a browser tab.
#>

[CmdletBinding()]
param(
    [int]$Port = 8787,
    [string]$Host = "127.0.0.1",
    [switch]$Reload,
    [switch]$Desktop
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
# The pinned requirements file is read as UTF-8. Without this, a
# machine with a legacy code page fails while reading the file.
$env:PYTHONUTF8 = "1"
& $Python -m pip install --disable-pip-version-check -q `
    -r (Join-Path $RepoRoot "backend\requirements.txt")

$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "$RepoRoot\backend;$RepoRoot\backend\vendor\readafp\src"

if ($Desktop) {
    # The desktop shell needs pywebview, which is not in the default
    # install because it pulls in a .NET dependency.
    & $Python -m pip install --disable-pip-version-check -q pywebview==6.2.1
    Write-Host "AFP Production Studio is opening in a desktop window." -ForegroundColor Green
    & $Python -m afpstudio --desktop
} else {
    $env:AFP_STUDIO_HOST = $Host
    $env:AFP_STUDIO_PORT = "$Port"
    Write-Host "AFP Production Studio is starting on http://${Host}:${Port}" -ForegroundColor Green
    if ($Reload) {
        & $Python -m afpstudio
    } else {
        & $Python -m afpstudio
    }
}
