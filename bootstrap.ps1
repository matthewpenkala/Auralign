# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

[CmdletBinding()]
param(
    [string]$Config = "examples/config.strict-windows-amd64-cpu.yaml",
    [string]$CacheDir,
    [string]$RuntimeDir,
    [switch]$Repair,
    [switch]$SourceCheckout
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RuntimeDir) { $RuntimeDir = Join-Path $PSScriptRoot ".auralign-runtime" }
if (-not $CacheDir) { $CacheDir = Join-Path $PSScriptRoot "models" }
if (-not [IO.Path]::IsPathRooted($Config)) { $Config = Join-Path $PSScriptRoot $Config }
$RuntimeDir = [IO.Path]::GetFullPath($RuntimeDir)
$CacheDir = [IO.Path]::GetFullPath($CacheDir)
$Config = [IO.Path]::GetFullPath($Config)

$arguments = @("prepare-runtime", "--config", $Config, "--cache-dir", $CacheDir)
if ($Repair) { $arguments += "--repair" }

if ($SourceCheckout) {
    if (-not (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) {
        throw "-SourceCheckout requires pyproject.toml beside bootstrap.ps1."
    }
    Push-Location $PSScriptRoot
    try {
        & uv run ltm @arguments
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
    exit 0
}

$installedLtm = Join-Path $RuntimeDir ".venv\Scripts\ltm.exe"
if (-not (Test-Path $installedLtm -PathType Leaf)) {
    throw "Install Auralign first or pass -SourceCheckout explicitly."
}

# The Python command owns acquisition, redaction, token removal, immutable-file
# verification, Punkt preparation, and strict offline backend startup.
& $installedLtm @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
