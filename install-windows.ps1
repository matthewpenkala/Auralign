# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

[CmdletBinding()]
param(
    [ValidateSet("Auto", "Cpu", "Cuda128")]
    [string]$Profile = "Auto",
    [string]$Destination = ".auralign-runtime",
    [string]$WheelPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Test-VcRuntime {
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    )
    foreach ($key in $keys) {
        if (Test-Path $key) {
            $installed = (Get-ItemProperty $key -ErrorAction SilentlyContinue).Installed
            if ($installed -eq 1) { return $true }
        }
    }
    return $false
}

if (-not [Environment]::Is64BitOperatingSystem -or
    [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne
        [Runtime.InteropServices.Architecture]::X64) {
    throw "Auralign 0.2.0 supports Windows x64 only."
}

Assert-Command "uv"
Assert-Command "ffprobe"
Assert-Command "ffmpeg"
if (-not (Test-VcRuntime)) {
    throw "Microsoft Visual C++ 2015-2022 x64 Redistributable is required."
}

$selected = $Profile
if ($Profile -eq "Auto") {
    $selected = if (Get-Command "nvidia-smi" -ErrorAction SilentlyContinue) { "Cuda128" } else { "Cpu" }
}
if ($selected -eq "Cuda128") {
    Assert-Command "nvidia-smi"
    if (-not $env:CUDA_PATH) {
        throw "CUDA_PATH is not set; install and configure the reviewed CUDA 12.8 toolkit first."
    }
    if ($env:CUDA_PATH -notmatch "v12\.8") {
        throw "Cuda128 requires CUDA 12.8; CUDA_PATH does not identify a 12.8 installation."
    }
    $cudnnCandidates = @(
        (Join-Path $env:CUDA_PATH "bin\cudnn64_9.dll")
    ) + (($env:PATH -split [IO.Path]::PathSeparator) | ForEach-Object {
        Join-Path $_ "cudnn64_9.dll"
    })
    if (-not ($cudnnCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1)) {
        throw "Cuda128 requires a discoverable cuDNN 9 x64 runtime separate from the CUDA toolkit."
    }
}

$python = (& uv python find 3.11 2>$null)
if (-not $python) {
    throw "Python 3.11 is required. Install it explicitly with 'uv python install 3.11'."
}
$pythonVersion = (& $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ($pythonVersion -ne "3.11") { throw "Resolved Python is $pythonVersion; Python 3.11 is required." }

$destinationPath = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
$venv = Join-Path $destinationPath ".venv"
& uv venv --python $python $venv
$env:VIRTUAL_ENV = $venv

$group = if ($selected -eq "Cuda128") { "cu128" } else { "cpu" }
$repoInstall = (Test-Path "pyproject.toml") -and (Test-Path "uv.lock")
if ($repoInstall) {
    & uv sync --active --locked --python (Join-Path $venv "Scripts\python.exe") --extra full --group $group --no-dev
} else {
    if (-not $WheelPath) {
        $WheelPath = Get-ChildItem -Path $PSScriptRoot -Filter "local_transcript_max-0.2.0-*.whl" |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $WheelPath -or -not (Test-Path $WheelPath)) {
        throw "The verified Auralign 0.2.0 wheel was not found."
    }
    $requirements = Join-Path $PSScriptRoot "requirements\windows-$($group)-py311.txt"
    if (-not (Test-Path $requirements)) {
        throw "The lock-derived Windows $group requirements manifest is missing."
    }
    & uv pip install --python (Join-Path $venv "Scripts\python.exe") --require-hashes -r $requirements
    & uv pip install --python (Join-Path $venv "Scripts\python.exe") --no-deps $WheelPath
}

$runtimePython = Join-Path $venv "Scripts\python.exe"
& $runtimePython -c "import faster_whisper, pyannote.audio, whisperx, speechbrain, torchcodec"
if ($selected -eq "Cuda128") {
    & $runtimePython -c @'
import ctranslate2
import torch
assert torch.cuda.is_available(), "PyTorch cannot start CUDA"
device = torch.cuda.get_device_properties(0)
capability = torch.cuda.get_device_capability(0)
assert any(arch == f"sm_{capability[0]}{capability[1]}" for arch in torch.cuda.get_arch_list()), (
    f"installed PyTorch does not contain code for {device.name} capability {capability}"
)
assert (torch.ones(1, device="cuda") + 1).item() == 2
assert ctranslate2.get_supported_compute_types("cuda"), "CTranslate2 has no CUDA compute type"
'@
}

Write-Host "Auralign 0.2.0 installed for Windows x64 profile $selected."
Write-Host "Model weights remain external; run bootstrap.ps1 only after reviewing upstream terms."
