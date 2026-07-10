#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Run privacy-bounded real validation on an owner-controlled trusted host."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from validation_capsule import EXPECTED_SPEAKERS, verify

from local_transcript_max.platform_security import ensure_private_directory


def checked(label: str, command: list[str], *, environment: dict[str, str]) -> None:
    completed = subprocess.run(
        command, text=True, capture_output=True, env=environment, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("validation artifact has an invalid shape")
    return payload


def create_local_tts(path: Path, environment: dict[str, str]) -> None:
    text = "Auralign validates local speech recognition, alignment, and diarization offline."
    if platform.system() == "Darwin":
        aiff = path.with_suffix(".aiff")
        checked("local TTS synthesis", ["say", "-o", str(aiff), text], environment=environment)
        checked(
            "local TTS conversion",
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(aiff),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(path),
            ],
            environment=environment,
        )
        aiff.unlink(missing_ok=True)
        return
    if platform.system() == "Windows":
        escaped_path = str(path).replace("'", "''")
        escaped_text = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{escaped_path}'); $s.Speak('{escaped_text}'); $s.Dispose()"
        )
        checked(
            "local TTS synthesis",
            ["powershell", "-NoProfile", "-Command", script],
            environment=environment,
        )
        return
    raise RuntimeError("trusted platform validation supports Windows and macOS only")


def validate_cuda(environment: dict[str, str]) -> None:
    code = """
import ctranslate2
import torch
assert torch.cuda.is_available()
major, minor = torch.cuda.get_device_capability(0)
assert "5090" in torch.cuda.get_device_name(0), "reviewed RTX 5090 host was not detected"
assert (major, minor) >= (12, 0), "reviewed Blackwell compute capability was not detected"
assert f"sm_{major}{minor}" in torch.cuda.get_arch_list()
assert (torch.ones(1, device="cuda") + 1).item() == 2
assert ctranslate2.get_supported_compute_types("cuda")
"""
    checked("CUDA 12.8 native probe", [sys.executable, "-c", code], environment=environment)


def assert_real_outputs(job: Path) -> None:
    metadata = load_json(job / "run_metadata.json")
    if metadata.get("alignment_backend") != "whisperx-forced":
        raise RuntimeError("real forced alignment backend was not used")
    transcript = load_json(job / "transcript.json")
    words = transcript.get("words")
    if not isinstance(words, list) or not words:
        raise RuntimeError("real ASR returned no aligned words")
    for word in words:
        if not isinstance(word, dict) or word.get("alignment_source") != "forced":
            raise RuntimeError("forced-alignment fallback or incomplete word detected")
        if not all(math.isfinite(float(word[key])) for key in ("start", "end")):
            raise RuntimeError("non-finite forced timestamp detected")
    exclusive = transcript.get("exclusive_diarization_turns")
    if not isinstance(exclusive, list) or not exclusive:
        raise RuntimeError("exclusive pyannote diarization output is missing")
    speakers = {str(turn.get("speaker")) for turn in exclusive if isinstance(turn, dict)}
    if len(speakers) != EXPECTED_SPEAKERS:
        raise RuntimeError("real-media structural speaker count did not match the capsule contract")
    embedding_report = load_json(job / "speaker_embedding_report.json")
    for row in embedding_report.get("speakers", {}).values():
        if not isinstance(row, dict) or not row.get("embedding_dimensions"):
            raise RuntimeError("ECAPA did not produce a usable embedding for each cluster")
    speaker_map = load_json(job / "speaker_map.json")
    labels = {
        row.get("label")
        for row in speaker_map.get("mappings", [])
        if isinstance(row, dict) and row.get("label") != "UNKNOWN"
    }
    if labels != {"canary-speaker-00", "canary-speaker-01"}:
        raise RuntimeError("held-out ECAPA identities did not both match their neutral enrollments")


def assert_tts_outputs(job: Path) -> None:
    metadata = load_json(job / "run_metadata.json")
    transcript = load_json(job / "transcript.json")
    words = transcript.get("words")
    if metadata.get("alignment_backend") != "whisperx-forced" or not isinstance(words, list):
        raise RuntimeError("local TTS did not exercise real ASR and forced alignment")
    if not words or any(
        not isinstance(word, dict) or word.get("alignment_source") != "forced"
        for word in words
    ):
        raise RuntimeError("local TTS forced-alignment output is incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("cpu", "cu128"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    capsule_value = os.environ.get("LTM_VALIDATION_CAPSULE_DIR")
    if not capsule_value:
        raise RuntimeError("runner-local capsule environment is not configured")
    capsule = Path(capsule_value)
    ensure_private_directory(capsule)
    verify(capsule)

    work = Path(".trusted-validation-work").resolve()
    if work.exists() or work.is_symlink():
        raise RuntimeError("trusted validation work directory must not preexist")
    work.mkdir(mode=0o700)
    environment = os.environ.copy()
    for name in ("HF_TOKEN", "LTM_HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        environment.pop(name, None)
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYANNOTE_METRICS_ENABLED": "false",
            "LTM_OFFLINE": "1",
            "LTM_SPEAKER_PROFILE_DIR": str(work / "profiles"),
        }
    )
    try:
        ltm = shutil.which("ltm")
        if ltm is None:
            raise RuntimeError("the locked Auralign CLI is not installed")
        if args.profile == "cu128":
            validate_cuda(environment)
        checked(
            "strict backend startup",
            [ltm, "doctor", "--startup", "--config", str(args.config)],
            environment=environment,
        )
        tts = work / "local-tts.wav"
        create_local_tts(tts, environment)
        checked(
            "local TTS real pipeline",
            [
                ltm,
                "transcribe",
                str(tts),
                "--config",
                str(args.config),
                "--out",
                str(work / "tts-job"),
                "--known-speakers",
                "1",
                "--speaker-id",
                "off",
            ],
            environment=environment,
        )
        assert_tts_outputs(work / "tts-job")
        for index in (0, 1):
            checked(
                "local ECAPA enrollment",
                [
                    ltm,
                    "speakers",
                    "enroll",
                    "--name",
                    f"canary-speaker-{index:02d}",
                    "--audio",
                    str(capsule / f"enrollment-speaker-{index:02d}.wav"),
                ],
                environment=environment,
            )
        canary_job = work / "canary-job"
        checked(
            "fixed real-media pipeline",
            [
                ltm,
                "transcribe",
                str(capsule / "canary-10m.mp4"),
                "--config",
                str(args.config),
                "--out",
                str(canary_job),
                "--known-speakers",
                "2",
            ],
            environment=environment,
        )
        assert_real_outputs(canary_job)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"trusted_platform_validation_ok=true profile={args.profile}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"trusted_platform_validation_ok=false reason={type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
