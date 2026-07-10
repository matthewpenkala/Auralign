# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

[CmdletBinding()]
param(
    [string]$Config = "examples/config.strict-windows-amd64-cpu.yaml",
    [string]$CacheDir = "models",
    [switch]$Repair
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$arguments = @(
    "prepare-runtime",
    "--config", $Config,
    "--cache-dir", $CacheDir
)
if ($Repair) { $arguments += "--repair" }

$installedLtm = Join-Path $PSScriptRoot ".auralign-runtime\.venv\Scripts\ltm.exe"
if (Test-Path $installedLtm) {
    $command = $installedLtm
} elseif (Get-Command "ltm" -ErrorAction SilentlyContinue) {
    $command = "ltm"
} elseif ((Get-Command "uv" -ErrorAction SilentlyContinue) -and (Test-Path "pyproject.toml")) {
    $command = "uv"
    $arguments = @("run", "ltm") + $arguments
} else {
    throw "Install Auralign first, then run bootstrap.ps1 from its bundle or source root."
}

# The Python command owns acquisition, redaction, token removal, immutable-file
# verification, Punkt preparation, and strict offline backend startup. Keeping
# those controls in one implementation prevents PowerShell/Bash policy drift.
& $command @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
