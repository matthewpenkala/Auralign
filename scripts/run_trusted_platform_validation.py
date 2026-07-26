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
import subprocess
import sys
from pathlib import Path
from typing import Any

from validation_capsule import EXPECTED_SPEAKERS, verify

from local_transcript_max.config import resolve_model_entry
from local_transcript_max.platform_security import ensure_private_directory
from local_transcript_max.speaker_id.enrollment import (
    PRODUCTION_EMBEDDING_BACKEND,
    load_profile_embeddings,
)
from local_transcript_max.speaker_id.matching import cosine_similarity


def checked(label: str, command: list[str], *, environment: dict[str, str]) -> None:
    child_environment = environment.copy()
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=child_environment,
        check=False,
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
    checked(
        "CUDA 12.8 native probe",
        [sys.executable, "-B", "-c", code],
        environment=environment,
    )


def _expected_device(profile: str) -> str:
    return "cuda" if profile == "cu128" else "cpu"


def _acceptance_profiles(profile_dir: Path) -> dict[str, tuple[str, list[float]]]:
    loaded = load_profile_embeddings(profile_dir)
    profiles = {
        str(row["name"]): (str(row["backend"]), list(row["embedding"])) for row in loaded.values()
    }
    if set(profiles) != {"canary-speaker-00", "canary-speaker-01"}:
        raise RuntimeError("held-out enrollment profile set is incomplete")
    return profiles


def _assert_identity_mapping(
    embeddings: dict[str, Any],
    speaker_map: dict[str, Any],
    *,
    enrolled_profiles: dict[str, tuple[str, list[float]]],
) -> None:
    threshold = speaker_map.get("threshold")
    margin = speaker_map.get("ambiguity_margin")
    if threshold != 0.75 or margin != 0.05:
        raise RuntimeError("held-out ECAPA acceptance thresholds drifted")
    if any(backend != PRODUCTION_EMBEDDING_BACKEND for backend, _ in enrolled_profiles.values()):
        raise RuntimeError("held-out enrollment did not use the production ECAPA backend")
    rows = speaker_map.get("mappings")
    if not isinstance(rows, list) or len(rows) != len(embeddings):
        raise RuntimeError("held-out ECAPA speaker map is incomplete")
    by_speaker: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("speaker"), str):
            raise RuntimeError("held-out ECAPA speaker map is malformed")
        speaker = str(row["speaker"])
        if speaker in by_speaker:
            raise RuntimeError("held-out ECAPA speaker map contains a duplicate cluster")
        by_speaker[speaker] = row
    if set(by_speaker) != set(embeddings):
        raise RuntimeError("held-out ECAPA speaker map does not cover every cluster")

    expected_labels: dict[str, str] = {}
    for speaker, raw_vector in embeddings.items():
        vector = [float(value) for value in raw_vector]
        ranked = sorted(
            (
                (max(0.0, cosine_similarity(vector, profile_vector)), label)
                for label, (_backend, profile_vector) in enrolled_profiles.items()
            ),
            reverse=True,
        )
        best_score, best_label = ranked[0]
        second_score = ranked[1][0]
        if best_score < threshold or best_score - second_score < margin:
            raise RuntimeError("held-out ECAPA identity is below threshold or ambiguous")
        expected_labels[speaker] = best_label
        row = by_speaker[speaker]
        confidence = row.get("confidence")
        if (
            row.get("label") != best_label
            or row.get("source_profile_id") != best_label
            or row.get("profile_backend") != PRODUCTION_EMBEDDING_BACKEND
            or row.get("ambiguous") is not False
            or row.get("reason") != "conservative_embedding_match"
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isclose(float(confidence), round(best_score, 6), abs_tol=1e-6)
        ):
            raise RuntimeError("speaker map disagrees with independent held-out ECAPA matching")
    if set(expected_labels.values()) != set(enrolled_profiles):
        raise RuntimeError("held-out ECAPA identities are not a one-to-one match")


def assert_real_outputs(
    job: Path,
    *,
    profile: str,
    enrolled_profiles: dict[str, tuple[str, list[float]]] | None = None,
    profile_dir: Path | None = None,
) -> None:
    expected_device = _expected_device(profile)
    metadata = load_json(job / "run_metadata.json")
    if metadata.get("alignment_backend") != "whisperx-forced":
        raise RuntimeError("real forced alignment backend was not used")
    if metadata.get("device") != expected_device:
        raise RuntimeError("ASR did not execute on the required profile device")
    expected_compute = "float16" if profile == "cu128" else "int8"
    if metadata.get("compute_type") != expected_compute:
        raise RuntimeError("ASR did not execute with the required compute type")
    if metadata.get("alignment_device") != expected_device:
        raise RuntimeError("WhisperX did not execute on the required profile device")
    if (
        metadata.get("diarization_resolved_device") != expected_device
        or metadata.get("diarization_device_fallback") is not False
    ):
        raise RuntimeError("pyannote did not execute on the required device without fallback")
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
    regular_diarization = load_json(job / "diarization.json").get("turns")
    exclusive_diarization = load_json(job / "exclusive_diarization.json").get("turns")
    if not isinstance(regular_diarization, list) or not regular_diarization:
        raise RuntimeError("regular pyannote diarization output is missing")
    if not isinstance(exclusive_diarization, list) or not exclusive_diarization:
        raise RuntimeError("exclusive pyannote diarization artifact is missing")
    embedding_report = load_json(job / "speaker_embedding_report.json")
    if embedding_report.get("device") != expected_device:
        raise RuntimeError("ECAPA did not execute on the required profile device")
    ecapa = resolve_model_entry("speaker-embedding", "ecapa")
    if ecapa is None or embedding_report.get("model_revision") != ecapa.revision:
        raise RuntimeError("ECAPA report does not identify the pinned model revision")
    for row in embedding_report.get("speakers", {}).values():
        if not isinstance(row, dict) or not row.get("embedding_dimensions"):
            raise RuntimeError("ECAPA did not produce a usable embedding for each cluster")
    embeddings = load_json(job / "speaker_cluster_embeddings.json")
    if set(embeddings) != speakers:
        raise RuntimeError("ECAPA cluster embeddings do not cover the diarized speakers")
    for vector in embeddings.values():
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("ECAPA returned an empty cluster vector")
        try:
            numeric = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("ECAPA returned a non-numeric cluster vector") from exc
        if not all(math.isfinite(value) for value in numeric):
            raise RuntimeError("ECAPA returned a non-finite cluster vector")
        norm = math.sqrt(math.fsum(value * value for value in numeric))
        if not math.isclose(norm, 1.0, abs_tol=1e-5):
            raise RuntimeError("ECAPA cluster vector is not unit-normalized")
    speaker_map = load_json(job / "speaker_map.json")
    if enrolled_profiles is None:
        if profile_dir is None:
            raise RuntimeError("speaker profile directory is not configured")
        enrolled_profiles = _acceptance_profiles(profile_dir)
    _assert_identity_mapping(
        embeddings,
        speaker_map,
        enrolled_profiles=enrolled_profiles,
    )


def assert_tts_outputs(job: Path) -> None:
    metadata = load_json(job / "run_metadata.json")
    transcript = load_json(job / "transcript.json")
    words = transcript.get("words")
    if metadata.get("alignment_backend") != "whisperx-forced" or not isinstance(words, list):
        raise RuntimeError("local TTS did not exercise real ASR and forced alignment")
    if not words or any(
        not isinstance(word, dict) or word.get("alignment_source") != "forced" for word in words
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
    work = ensure_private_directory(work)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
        ltm = Path(sys.executable).with_name("ltm.exe" if os.name == "nt" else "ltm")
        if not ltm.is_file():
            raise RuntimeError("the locked Auralign CLI is not installed")
        if args.profile == "cu128":
            validate_cuda(environment)
        checked(
            "strict backend startup",
            [str(ltm), "doctor", "--startup", "--config", str(args.config)],
            environment=environment,
        )
        tts = work / "local-tts.wav"
        create_local_tts(tts, environment)
        checked(
            "local TTS real pipeline",
            [
                str(ltm),
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
                    str(ltm),
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
                str(ltm),
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
        assert_real_outputs(
            canary_job,
            profile=args.profile,
            profile_dir=work / "profiles",
        )
    finally:
        import shutil

        shutil.rmtree(work)
        if work.exists() or work.is_symlink():
            raise RuntimeError("private validation outputs could not be removed")
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
