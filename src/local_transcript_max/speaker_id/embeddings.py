# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Local speaker embedding extraction.

SpeechBrain is used when installed. The deterministic waveform-stat fallback is
only for local smoke tests and low-confidence bookkeeping; it is not marketed as
production-grade identity matching.
"""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import wave
from pathlib import Path
from typing import Any

from local_transcript_max.config import default_model_cache, resolve_model_entry
from local_transcript_max.models import status_for

from .matching import validate_embedding

MAX_EMBEDDING_FILE_BYTES = 1024 * 1024


def extract_embedding(
    audio_path: Path,
    *,
    allow_fallback: bool = False,
    cache_dir: Path | None = None,
) -> tuple[str, list[float]]:
    try:
        return _speechbrain_embedding(audio_path, cache_dir=cache_dir or default_model_cache())
    except Exception as exc:
        if not allow_fallback:
            raise RuntimeError(
                "SpeechBrain ECAPA speaker embedding is unavailable. "
                "Run: uv sync --extra speaker-id and pull speechbrain/spkrec-ecapa-voxceleb, "
                "or use explicit degraded fallback mode for tests. "
                f"Reason: {exc}"
            ) from exc
        return "waveform-stats", _waveform_stats_embedding(audio_path)


def save_embedding(path: Path, backend: str, vector: list[float]) -> None:
    validated = validate_embedding(vector)
    if not backend.strip():
        raise ValueError("Embedding backend must not be empty.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Embedding files cannot be symlinks.")
    payload = (
        json.dumps({"backend": backend, "embedding": validated}, indent=2, sort_keys=True) + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=".embedding.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def load_embedding(path: Path) -> tuple[str, list[float]]:
    if path.is_symlink():
        raise ValueError("Embedding path must be a regular file, not a symlink.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Embedding path must be a regular file, not a symlink.") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Embedding path must be a regular file, not a symlink.")
        if file_stat.st_size > MAX_EMBEDDING_FILE_BYTES:
            raise ValueError("Embedding file exceeds the size limit.")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            encoded = handle.read(MAX_EMBEDDING_FILE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(encoded.encode("utf-8")) > MAX_EMBEDDING_FILE_BYTES:
        raise ValueError("Embedding file exceeds the size limit.")
    payload: Any = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError("Embedding file must contain a JSON object.")
    backend = payload.get("backend")
    raw_vector = payload.get("embedding")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("Embedding backend must be a non-empty string.")
    if not isinstance(raw_vector, list):
        raise ValueError("Embedding vector must be a JSON array.")
    return backend, validate_embedding(raw_vector)


def _speechbrain_embedding(audio_path: Path, *, cache_dir: Path) -> tuple[str, list[float]]:
    entry = resolve_model_entry("speaker-embedding", "ecapa")
    if entry is None:  # pragma: no cover - manifest invariant
        raise RuntimeError("Speaker embedding manifest entry 'ecapa' is missing.")
    status = status_for(entry, cache_dir)
    if not status.present:
        raise RuntimeError(status.problem_summary(cache_dir))

    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import FetchConfig

    classifier = EncoderClassifier.from_hparams(
        source=str(status.path),
        savedir=str(status.path),
        fetch_config=FetchConfig(allow_network=False),
    )
    signal = classifier.load_audio(str(audio_path))
    embeddings = classifier.encode_batch(signal)
    vector = embeddings.squeeze().detach().cpu().tolist()
    if isinstance(vector, float):
        vector = [vector]
    if not isinstance(vector, list):
        raise ValueError("SpeechBrain ECAPA returned an invalid embedding shape.")
    return "speechbrain-ecapa", validate_embedding(vector, label="SpeechBrain embedding")


def _waveform_stats_embedding(audio_path: Path) -> list[float]:
    with wave.open(str(audio_path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        width = wav.getsampwidth()
    if width != 2 or not frames:
        return [0.0, 0.0, 0.0, 0.0]
    values = [
        int.from_bytes(frames[index : index + 2], "little", signed=True) / 32768.0
        for index in range(0, len(frames), 2)
    ]
    mean = sum(values) / len(values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    peak = max(abs(value) for value in values)
    crossings = sum(
        1 for a, b in zip(values, values[1:], strict=False) if (a < 0 <= b) or (b < 0 <= a)
    )
    zcr = crossings / max(1, len(values) - 1)
    return [mean, rms, peak, zcr]
