# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

[CmdletBinding()]
param(
    [ValidateSet("Auto", "Cpu", "Cuda128")]
    [string]$Profile = "Auto",
    [string]$Destination,
    [string]$WheelPath,
    [switch]$SourceCheckout
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Command([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Required command '$Name' was not found on PATH."
    }
    return $command.Source
}

function Invoke-Native([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Native command '$Command' failed with exit code $LASTEXITCODE."
    }
}

function Test-Native([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments *> $null
    return $LASTEXITCODE -eq 0
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

function Test-Cuda128Prerequisites {
    $nvidiaSmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi -or -not (Test-Native $nvidiaSmi.Source @("--query-gpu=name", "--format=csv,noheader"))) {
        return $false
    }
    if (-not $env:CUDA_PATH -or $env:CUDA_PATH -notmatch "v12\.8(?:\\|$)") {
        return $false
    }
    if (-not (Test-Path (Join-Path $env:CUDA_PATH "bin\cublas64_12.dll") -PathType Leaf)) {
        return $false
    }
    $cudnnCandidates = @(
        (Join-Path $env:CUDA_PATH "bin\cudnn64_9.dll")
    ) + (($env:PATH -split [IO.Path]::PathSeparator) | Where-Object {
        $_ -and $_.Trim()
    } | ForEach-Object { Join-Path $_ "cudnn64_9.dll" })
    return [bool]($cudnnCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1)
}

if (-not [Environment]::Is64BitOperatingSystem -or
    [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne
        [Runtime.InteropServices.Architecture]::X64) {
    throw "Auralign 0.2.0 supports Windows x64 only."
}

$uv = Assert-Command "uv"
$ffprobe = Assert-Command "ffprobe"
$ffmpeg = Assert-Command "ffmpeg"
Invoke-Native $ffprobe @("-version")
Invoke-Native $ffmpeg @("-version")
if (-not (Test-VcRuntime)) {
    throw "Microsoft Visual C++ 2015-2022 x64 Redistributable is required."
}

$cudaReady = Test-Cuda128Prerequisites
$selected = $Profile
if ($Profile -eq "Auto") {
    $selected = if ($cudaReady) { "Cuda128" } else { "Cpu" }
} elseif ($Profile -eq "Cuda128" -and -not $cudaReady) {
    throw "Cuda128 requires a working NVIDIA probe, CUDA 12.8, cuBLAS 12, and cuDNN 9 x64."
}

$python = (& $uv python find 3.11 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $python) {
    throw "Python 3.11 is required. Install it explicitly with 'uv python install 3.11'."
}
$pythonVersion = (& $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "3.11") {
    throw "Resolved Python is not Python 3.11."
}

if (-not $Destination) {
    $Destination = Join-Path $PSScriptRoot ".auralign-runtime"
}
$destinationPath = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
$venv = Join-Path $destinationPath ".venv"
Invoke-Native $uv @("venv", "--python", $python, $venv)
$env:VIRTUAL_ENV = $venv

$group = if ($selected -eq "Cuda128") { "cu128" } else { "cpu" }
$runtimePython = Join-Path $venv "Scripts\python.exe"
if ($SourceCheckout) {
    if (-not (Test-Path (Join-Path $PSScriptRoot "pyproject.toml")) -or
        -not (Test-Path (Join-Path $PSScriptRoot "uv.lock"))) {
        throw "-SourceCheckout requires pyproject.toml and uv.lock beside this installer."
    }
    Push-Location $PSScriptRoot
    try {
        Invoke-Native $uv @(
            "sync", "--active", "--locked", "--python", $runtimePython,
            "--extra", "full", "--group", $group, "--no-dev"
        )
    } finally {
        Pop-Location
    }
} else {
    if (-not $WheelPath) {
        $wheels = @(Get-ChildItem -Path $PSScriptRoot -Filter "local_transcript_max-0.2.0-py3-none-any.whl")
        if ($wheels.Count -ne 1) {
            throw "The bundle must contain exactly one verified Auralign 0.2.0 wheel."
        }
        $WheelPath = $wheels[0].FullName
    }
    if (-not (Test-Path $WheelPath -PathType Leaf)) {
        throw "The verified Auralign 0.2.0 wheel was not found."
    }
    $requirements = Join-Path $PSScriptRoot "requirements\windows-$($group)-py311.txt"
    if (-not (Test-Path $requirements -PathType Leaf)) {
        throw "The lock-derived Windows $group requirements manifest is missing."
    }
    # Every candidate remains hash-pinned. Considering all declared indexes is
    # required because uv's first-index policy can otherwise stop at a PyTorch
    # index that contains a package name but not the reviewed PyPI version.
    Invoke-Native $uv @(
        "pip", "install", "--python", $runtimePython,
        "--index-strategy", "unsafe-best-match", "--require-hashes", "-r", $requirements
    )
    Invoke-Native $uv @("pip", "install", "--python", $runtimePython, "--no-deps", $WheelPath)
}

Invoke-Native $runtimePython @("-c", "import faster_whisper, pyannote.audio, whisperx, speechbrain, torchcodec")
if ($selected -eq "Cuda128") {
    $cudaProbe = @'
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
    Invoke-Native $runtimePython @("-c", $cudaProbe)
} else {
    Invoke-Native $runtimePython @(
        "-c",
        "import ctranslate2, torch; assert (torch.ones(1) + 1).item() == 2; assert ctranslate2.get_supported_compute_types('cpu')"
    )
}

Write-Host "Auralign 0.2.0 installed for Windows x64 profile $selected."
Write-Host "Runtime: $destinationPath"
Write-Host "Model weights remain external; run bootstrap.ps1 only after reviewing upstream terms."
