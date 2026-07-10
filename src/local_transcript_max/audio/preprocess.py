# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Preprocessing orchestration."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from local_transcript_max.config import AudioConfig
from local_transcript_max.schemas import AudioMetadata

from .inspect import inspect_media
from .normalize import normalize_audio


def prepare_canonical_audio(
    input_path: Path,
    out_dir: Path,
    config: AudioConfig,
    *,
    input_metadata: AudioMetadata | None = None,
) -> tuple[Path, list[str]]:
    canonical = out_dir / "canonical.wav"
    metadata = input_metadata or inspect_media(input_path)
    if can_reuse_as_canonical(metadata, config):
        canonical.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(input_path, canonical)
        canonical.chmod(0o600)
        return canonical, ["reuse-canonical-wav", str(input_path), str(canonical)]
    command = normalize_audio(input_path, canonical, config)
    canonical.chmod(0o600)
    return canonical, command


def canonical_audio_decision(
    input_path: Path,
    canonical_path: Path,
    metadata: AudioMetadata,
    config: AudioConfig,
    command: list[str],
) -> dict[str, object]:
    input_matched = can_reuse_as_canonical(metadata, config)
    reused = bool(command and command[0] == "reuse-canonical-wav")
    return {
        "action": "reused" if reused else "normalized",
        "reused": reused,
        "reason": "already_canonical_wav" if reused else canonical_reuse_blocker(metadata, config),
        "ffmpeg_invoked": not reused,
        "input_matched_canonical_requirements": input_matched,
        "input_path": str(input_path),
        "canonical_path": str(canonical_path),
        "sample_rate": metadata.sample_rate,
        "channels": metadata.channels,
        "codec": metadata.codec,
        "format_name": metadata.format_name,
    }


def can_reuse_as_canonical(metadata: AudioMetadata, config: AudioConfig) -> bool:
    return canonical_reuse_blocker(metadata, config) is None


def canonical_reuse_blocker(metadata: AudioMetadata, config: AudioConfig) -> str | None:
    if config.normalize_loudness or config.denoise != "off" or config.separate_vocals != "off":
        return "audio_processing_requested"
    expected_channels = 1 if config.mono and not config.preserve_channels else metadata.channels
    if metadata.sample_rate != config.sample_rate:
        return "sample_rate_mismatch"
    if metadata.channels != expected_channels:
        return "channel_layout_mismatch"
    if metadata.codec != "pcm_s16le":
        return "codec_mismatch"
    if metadata.format_name is None or "wav" not in metadata.format_name:
        return "container_mismatch"
    return None


def atomic_copy(source: Path, destination: Path) -> None:
    """Copy ``source`` to an independent file and atomically publish it."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
