#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Run privacy-safe real ASR acceptance for every native model pack and preset.

This is a controlled release-machine gate. It performs no acquisition and
accepts no repository identifiers as runtime model inputs. Instead, it:

1. resolves every production native-download model from explicit local source
   roots;
2. assembles and strictly verifies one deterministic private model pack per
   model;
3. launches a fresh ``python -B`` worker for every model and normal preset;
4. denies Python network access and enables each upstream offline mode; and
5. writes a portable JSON record containing no local path, transcript, audio
   hash, credential, cache marker, stdout, or stderr.

Source roots may be separate. A model is accepted only when exactly one root
contains either its canonical directory name or its manifest key. This avoids
copying controlled caches into a synthetic aggregate cache and fails closed on
missing or ambiguous inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import wave
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, NoReturn, cast

from local_transcript_max import models as model_cache_authority
from local_transcript_max.config import (
    MODEL_MANIFEST,
    ModelEntry,
    PipelineConfig,
    PresetName,
    preset_config,
)
from local_transcript_max.model_delivery import (
    MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
    MODEL_DELIVERY_CATALOG_VERSION,
    NORMAL_PRESET_MODELS,
    model_delivery_catalog_sha256,
)
from local_transcript_max.model_pack import (
    MODEL_PACK_LAYOUT_VERSION,
    MODEL_PACK_SCHEMA_VERSION,
    assemble_model_pack,
    verify_model_pack,
)
from local_transcript_max.platform_security import (
    ensure_private_directory,
    open_regular_nofollow,
    reject_path_indirection,
)

ACCEPTANCE_SCHEMA_VERSION: Final = "auralign-native-model-acceptance.v1"
WORKER_RESULT_SCHEMA_VERSION: Final = "auralign-native-model-worker-result.v1"
DEFAULT_OUTPUT_FILENAME: Final = "native-model-acceptance.json"
_WORKER_FLAG: Final = "--_worker"
_ACQUISITION_MARKER: Final = ".ltm-model.json"
_READ_BLOCK_SIZE: Final = 8 * 1024 * 1024
_MAX_FIXTURE_BYTES: Final = 64 * 1024 * 1024
_PRIVATE_TEXT_MARKERS: Final = (
    "/" "Users/",
    "/" "home/",
    "/private/" "tmp/",
    "/private/" "var/folders/",
    "/tmp/",
    "file://",
    "authorization: bearer",
    "hf_token",
    "ltm_hf_token",
    "password=",
    "secret=",
    "token=",
    ".ltm-model.json",
)
_WORKER_ENVIRONMENT_KEYS: Final = {
    "audio": "AURALIGN_ACCEPTANCE_AUDIO",
    "case_kind": "AURALIGN_ACCEPTANCE_CASE_KIND",
    "model_key": "AURALIGN_ACCEPTANCE_MODEL_KEY",
    "packs_root": "AURALIGN_ACCEPTANCE_PACKS_ROOT",
    "preset": "AURALIGN_ACCEPTANCE_PRESET",
    "result": "AURALIGN_ACCEPTANCE_RESULT",
}
_OFFLINE_ENVIRONMENT: Final = {
    "DO_NOT_TRACK": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "LTM_OFFLINE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_REMOVED_WORKER_ENVIRONMENT: Final = {
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "LTM_HF_TOKEN",
    "LTM_ASR_BACKEND",
    "LTM_ASR_BATCHED",
    "LTM_ASR_BATCH_SIZE",
    "LTM_ASR_CPU_THREADS",
    "LTM_ASR_DEVICE",
    "LTM_ASR_MLX_FP16",
    "LTM_ASR_MLX_MODEL",
    "LTM_ASR_MLX_MODEL_PATH",
    "LTM_ASR_MLX_VAD_WINDOWED",
    "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS",
    "LTM_ASR_MLX_VAD_WINDOW_SECONDS",
    "LTM_ASR_NUM_WORKERS",
    "LTM_DEVICE",
    "LTM_MODEL_CACHE",
    "LTM_MODEL_LIFECYCLE",
}

CaseKind = Literal["model", "preset"]


class NativeModelAcceptanceError(RuntimeError):
    """Raised when model-pack or real-inference acceptance fails closed."""


class NetworkDeniedError(RuntimeError):
    """Raised when a worker attempts Internet or IP-socket access."""


@dataclass(frozen=True)
class CaseSpec:
    case_kind: CaseKind
    model_key: str
    preset: str | None = None

    @property
    def case_id(self) -> str:
        if self.case_kind == "preset":
            return f"preset:{self.preset}"
        return f"model:{self.model_key}"


@dataclass
class NetworkGuard:
    denied_attempts: int = 0

    def reject(self) -> NoReturn:
        self.denied_attempts += 1
        raise NetworkDeniedError("network access is disabled for native model acceptance")


def create_acceptance_work_directory(path: Path) -> Path:
    """Create one absent task root with the exact current process identity.

    macOS temporary-directory parents may be owned by the current user but
    retain the ``wheel`` group.  Children can inherit that group even though
    the release pack contract requires the current effective uid *and* gid.
    Normalize only this newly created, task-owned root before any pack
    destination parents are created below it.
    """

    directory = _absolute_path(path)
    if os.path.lexists(directory):
        raise NativeModelAcceptanceError("acceptance work directory must be absent")
    directory = ensure_private_directory(directory)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(directory, flags)
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(initial.st_mode)
            or initial.st_uid != os.geteuid()
        ):
            raise NativeModelAcceptanceError(
                "acceptance work directory has an unsafe owner or type"
            )
        os.fchown(descriptor, os.geteuid(), os.getegid())
        os.fchmod(descriptor, 0o700)
        sealed = os.fstat(descriptor)
        path_metadata = _safe_lstat(directory, "acceptance work directory")
        if (
            not stat.S_ISDIR(sealed.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or sealed.st_uid != os.geteuid()
            or sealed.st_gid != os.getegid()
            or stat.S_IMODE(sealed.st_mode) != 0o700
            or (sealed.st_dev, sealed.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise NativeModelAcceptanceError(
                "acceptance work directory could not be sealed safely"
            )
    except OSError as exc:
        raise NativeModelAcceptanceError(
            "acceptance work directory could not be sealed safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return directory


def native_download_entries(
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> tuple[ModelEntry, ...]:
    """Return the exact sixteen production-selectable native pack authorities."""

    entries = tuple(entry for entry in manifest.values() if entry.delivery == "native-download")
    if len(entries) != 16:
        raise NativeModelAcceptanceError("native model authority must contain exactly 16 packs")
    if any(
        not entry.production_selectable
        or entry.role not in {"asr", "asr-mlx"}
        or entry.package_bundle_id is None
        or entry.package_bundle_version is None
        or entry.package_relative_path is None
        for entry in entries
    ):
        raise NativeModelAcceptanceError("native model authority is incomplete")
    return entries


def acceptance_cases(
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> tuple[CaseSpec, ...]:
    model_cases = tuple(
        CaseSpec(case_kind="model", model_key=entry.key)
        for entry in native_download_entries(manifest)
    )
    preset_cases = tuple(
        CaseSpec(case_kind="preset", model_key=model_key, preset=preset)
        for preset, model_key in NORMAL_PRESET_MODELS.items()
    )
    if len(preset_cases) != 4:
        raise NativeModelAcceptanceError("normal preset authority must contain exactly four cases")
    if any(case.model_key not in manifest for case in preset_cases):
        raise NativeModelAcceptanceError("normal preset selects an unknown model")
    return (*model_cases, *preset_cases)


def resolve_source_models(
    source_roots: Sequence[Path],
    *,
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> dict[str, Path]:
    """Resolve every native model to exactly one explicit local source root."""

    if not source_roots:
        raise NativeModelAcceptanceError("at least one explicit source root is required")
    normalized_roots: list[Path] = []
    seen_root_paths: set[str] = set()
    for untyped_root in source_roots:
        root = reject_path_indirection(
            Path(os.path.abspath(os.fspath(untyped_root.expanduser())))
        )
        root_key = os.fspath(root)
        if root_key in seen_root_paths:
            raise NativeModelAcceptanceError("source roots must not be duplicated")
        seen_root_paths.add(root_key)
        metadata = _safe_lstat(root, "source root")
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise NativeModelAcceptanceError(
                "each source root must be a safe current-user directory"
            )
        normalized_roots.append(root)

    resolved: dict[str, Path] = {}
    for entry in native_download_entries(manifest):
        candidates: list[Path] = []
        candidate_names = tuple(dict.fromkeys((entry.directory_name, entry.key)))
        for root in normalized_roots:
            for name in candidate_names:
                candidate = root / name
                if os.path.lexists(candidate):
                    candidates.append(candidate)
        if len(candidates) != 1:
            state = "missing" if not candidates else "ambiguous"
            raise NativeModelAcceptanceError(
                f"native model source is {state}: {entry.key}"
            )
        resolved[entry.key] = candidates[0]
    if set(resolved) != {entry.key for entry in native_download_entries(manifest)}:
        raise NativeModelAcceptanceError("native model source mapping is incomplete")
    return resolved


def assemble_verified_packs(
    source_models: Mapping[str, Path],
    packs_root: Path,
    transient_root: Path,
    *,
    license_directory: Path,
    manifest: Mapping[str, ModelEntry] = MODEL_MANIFEST,
) -> dict[str, dict[str, Any]]:
    """Assemble exact packs through a transient view whenever normalization is needed."""

    packs_root = ensure_private_directory(packs_root)
    transient_root = ensure_private_directory(transient_root)
    identities: dict[str, dict[str, Any]] = {}
    for entry in native_download_entries(manifest):
        source = source_models.get(entry.key)
        if source is None:
            raise NativeModelAcceptanceError(f"native model source is missing: {entry.key}")
        source_kind = _classify_source_tree(source, entry)
        exact_source = source
        filtered_source: Path | None = None
        if (
            source_kind == "acquisition-cache"
            or _source_requires_transient_view(source, entry)
        ):
            filtered_source = transient_root / entry.key
            _copy_exact_model_tree(source, filtered_source, entry)
            exact_source = filtered_source
        bundle_parent = ensure_private_directory(
            packs_root / cast(str, entry.package_bundle_id)
        )
        destination = bundle_parent / cast(str, entry.package_bundle_version)
        try:
            assembled = assemble_model_pack(
                exact_source,
                destination,
                entry=entry,
                license_directory=license_directory,
            )
            verified = verify_model_pack(destination, entry=entry)
            if assembled != verified:
                raise NativeModelAcceptanceError(
                    f"assembled pack identity changed: {entry.key}"
                )
            identities[entry.key] = verified
        finally:
            if filtered_source is not None and os.path.lexists(filtered_source):
                shutil.rmtree(filtered_source)
    if set(identities) != set(source_models):
        raise NativeModelAcceptanceError("assembled pack set does not match source mapping")
    return identities


def _classify_source_tree(source: Path, entry: ModelEntry) -> Literal["exact", "acquisition-cache"]:
    directories, files = _scan_source_tree(source)
    expected_directories = {"."}
    for required in entry.required_files:
        parent = PurePosixPath(required).parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if directories != expected_directories:
        raise NativeModelAcceptanceError(f"model source directory contract drift: {entry.key}")
    actual_files = set(files)
    expected_files = set(entry.required_files)
    if actual_files == expected_files:
        return "exact"
    if actual_files == {*expected_files, _ACQUISITION_MARKER}:
        return "acquisition-cache"
    raise NativeModelAcceptanceError(f"model source file contract drift: {entry.key}")


def _source_requires_transient_view(source: Path, entry: ModelEntry) -> bool:
    """Return whether an exact source needs private ownership/mode normalization."""

    expected_owner = (os.geteuid(), os.getegid())
    unsafe_mode_bits = 0o022 | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    expected_paths: dict[Path, bool] = {source: True}
    for relative_path in entry.required_files:
        candidate = source.joinpath(*PurePosixPath(relative_path).parts)
        expected_paths[candidate] = False
        parent = candidate.parent
        while parent != source:
            expected_paths[parent] = True
            parent = parent.parent
    for path, is_directory in expected_paths.items():
        metadata = _safe_lstat(path, "model source entry")
        expected_type = (
            stat.S_ISDIR(metadata.st_mode)
            if is_directory
            else stat.S_ISREG(metadata.st_mode)
        )
        if stat.S_ISLNK(metadata.st_mode) or not expected_type:
            raise NativeModelAcceptanceError("model source tree is linked or irregular")
        if (
            (metadata.st_uid, metadata.st_gid) != expected_owner
            or stat.S_IMODE(metadata.st_mode) & unsafe_mode_bits
        ):
            return True
    return False


def _scan_source_tree(root: Path) -> tuple[set[str], dict[str, Path]]:
    root_metadata = _safe_lstat(root, "model source root")
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or root_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(root_metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise NativeModelAcceptanceError("model source root is unsafe")
    root_device = root_metadata.st_dev
    directories = {"."}
    files: dict[str, Path] = {}
    pending: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while pending:
        directory, logical_directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise NativeModelAcceptanceError("model source directory is unreadable") from exc
        for child in children:
            metadata = _safe_lstat(child, "model source entry")
            relative = (
                PurePosixPath(child.name)
                if logical_directory == PurePosixPath(".")
                else logical_directory / child.name
            )
            logical_path = relative.as_posix()
            if (
                metadata.st_dev != root_device
                or metadata.st_uid != os.geteuid()
                or stat.S_ISLNK(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise NativeModelAcceptanceError("model source tree is linked or unsafe")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(logical_path)
                pending.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise NativeModelAcceptanceError("model source contains a hardlinked file")
                files[logical_path] = child
            else:
                raise NativeModelAcceptanceError(
                    "model source contains a special filesystem object"
                )
    return directories, files


def _copy_exact_model_tree(source: Path, destination: Path, entry: ModelEntry) -> None:
    if os.path.lexists(destination):
        raise NativeModelAcceptanceError("transient exact model destination must be absent")
    destination.mkdir(mode=0o700)
    try:
        for relative_path in entry.required_files:
            output = destination.joinpath(*PurePosixPath(relative_path).parts)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            output.parent.chmod(0o700)
            _clone_or_copy_exact_file(
                source.joinpath(*PurePosixPath(relative_path).parts),
                output,
                expected_size=entry.expected_size_map[relative_path],
                expected_sha256=entry.expected_digest_map[relative_path],
            )
        if _classify_source_tree(destination, entry) != "exact":
            raise NativeModelAcceptanceError(
                f"transient model source did not close exactly: {entry.key}"
            )
    except BaseException:
        if os.path.lexists(destination):
            shutil.rmtree(destination)
        raise


def _clone_or_copy_exact_file(
    source: Path,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """Create one exact non-linked file, preferring an APFS clone on Darwin."""

    if os.path.lexists(destination):
        raise NativeModelAcceptanceError("transient model file destination must be absent")
    cloned = False
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["/bin/cp", "-c", os.fspath(source), os.fspath(destination)],
            check=False,
            capture_output=True,
        )
        cloned = completed.returncode == 0
    if not cloned:
        source_descriptor = _open_readonly_nofollow(source)
        destination_descriptor = -1
        try:
            before = os.fstat(source_descriptor)
            if before.st_nlink != 1 or before.st_size != expected_size:
                raise NativeModelAcceptanceError("source model file contract changed")
            destination_descriptor = open_regular_nofollow(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            while True:
                chunk = os.read(source_descriptor, _READ_BLOCK_SIZE)
                if not chunk:
                    break
                _write_all(destination_descriptor, chunk)
            os.fsync(destination_descriptor)
            after = os.fstat(source_descriptor)
            if _stat_identity(before) != _stat_identity(after):
                raise NativeModelAcceptanceError("source model file changed during import")
        finally:
            os.close(source_descriptor)
            if destination_descriptor >= 0:
                os.close(destination_descriptor)
    destination.chmod(0o600)
    record = _hash_regular_file(destination)
    if record != {"size": expected_size, "sha256": expected_sha256}:
        destination.unlink(missing_ok=True)
        raise NativeModelAcceptanceError("transient model file does not match authority")


def copy_and_describe_fixture(
    source: Path,
    destination: Path,
    *,
    maximum_seconds: float,
) -> dict[str, Any]:
    """Copy one bounded WAV into private work state without publishing its hash."""

    source = reject_path_indirection(source)
    source_descriptor = _open_readonly_nofollow(source)
    destination_descriptor = -1
    try:
        before = os.fstat(source_descriptor)
        if before.st_nlink != 1 or before.st_size <= 0 or before.st_size > _MAX_FIXTURE_BYTES:
            raise NativeModelAcceptanceError("audio fixture size contract is invalid")
        destination_descriptor = open_regular_nofollow(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        while True:
            chunk = os.read(source_descriptor, _READ_BLOCK_SIZE)
            if not chunk:
                break
            _write_all(destination_descriptor, chunk)
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise NativeModelAcceptanceError("audio fixture changed during private copy")
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
    try:
        with wave.open(os.fspath(destination), "rb") as handle:
            channels = handle.getnchannels()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            sample_width = handle.getsampwidth()
            compression = handle.getcomptype()
    except (OSError, EOFError, wave.Error) as exc:
        raise NativeModelAcceptanceError("audio fixture must be a readable PCM WAV") from exc
    duration_seconds = frame_count / sample_rate if sample_rate else 0.0
    if (
        compression != "NONE"
        or channels not in {1, 2}
        or sample_rate < 8_000
        or sample_rate > 96_000
        or sample_width not in {1, 2, 3, 4}
        or duration_seconds <= 0.0
        or duration_seconds > maximum_seconds
    ):
        raise NativeModelAcceptanceError("audio fixture format or duration is out of bounds")
    return {
        "kind": "operator-controlled-local-pcm-wav",
        "channels": channels,
        "sample_rate_hz": sample_rate,
        "sample_width_bytes": sample_width,
        "duration_milliseconds": round(duration_seconds * 1000),
        "content_and_hash_withheld": True,
    }


def run_inference_cases(
    cases: Sequence[CaseSpec],
    *,
    packs_root: Path,
    audio_path: Path,
    worker_root: Path,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run every case in a fresh bytecode-disabled, network-denied process."""

    model_results: list[dict[str, Any]] = []
    preset_results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        result_path = worker_root / f"result-{index:02d}.json"
        environment = worker_environment(
            case,
            packs_root=packs_root,
            audio_path=audio_path,
            result_path=result_path,
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-B", os.fspath(Path(__file__).resolve()), _WORKER_FLAG],
                check=False,
                capture_output=True,
                env=environment,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise NativeModelAcceptanceError(
                f"native model inference timed out: {case.case_id}"
            ) from exc
        result = _read_worker_result(result_path)
        if completed.returncode != 0 or result.get("status") != "pass":
            error_type = result.get("error_type", "worker-failure")
            raise NativeModelAcceptanceError(
                f"native model inference failed: {case.case_id}:{error_type}"
            )
        if completed.stdout or completed.stderr:
            # Raw backend output can contain transcript text or local paths. It
            # is intentionally neither persisted nor summarized.
            result["backend_output_withheld"] = True
        result_path.unlink()
        if case.case_kind == "model":
            model_results.append(result)
        else:
            preset_results.append(result)
    return model_results, preset_results


def worker_environment(
    case: CaseSpec,
    *,
    packs_root: Path,
    audio_path: Path,
    result_path: Path,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    for key in tuple(environment):
        if key in _REMOVED_WORKER_ENVIRONMENT or key.startswith("LTM_ASR_"):
            environment.pop(key, None)
    environment.update(_OFFLINE_ENVIRONMENT)
    environment[_WORKER_ENVIRONMENT_KEYS["audio"]] = os.fspath(audio_path)
    environment[_WORKER_ENVIRONMENT_KEYS["case_kind"]] = case.case_kind
    environment[_WORKER_ENVIRONMENT_KEYS["model_key"]] = case.model_key
    environment[_WORKER_ENVIRONMENT_KEYS["packs_root"]] = os.fspath(packs_root)
    environment[_WORKER_ENVIRONMENT_KEYS["preset"]] = case.preset or ""
    environment[_WORKER_ENVIRONMENT_KEYS["result"]] = os.fspath(result_path)
    return environment


def worker_main() -> int:
    result_path_value = os.environ.get(_WORKER_ENVIRONMENT_KEYS["result"])
    if not result_path_value:
        return 2
    result_path = Path(result_path_value)
    case = CaseSpec(
        case_kind=_case_kind_from_environment(),
        model_key=_required_worker_environment("model_key"),
        preset=os.environ.get(_WORKER_ENVIRONMENT_KEYS["preset"]) or None,
    )
    try:
        payload = run_worker_case(
            case,
            packs_root=Path(_required_worker_environment("packs_root")),
            audio_path=Path(_required_worker_environment("audio")),
        )
    except Exception as exc:
        payload = {
            "schema_version": WORKER_RESULT_SCHEMA_VERSION,
            "status": "failed",
            "case_id": case.case_id,
            "error_type": type(exc).__name__,
            "diagnostic_text_withheld": True,
        }
        _write_private_json_exclusive(result_path, payload)
        return 1
    _write_private_json_exclusive(result_path, payload)
    return 0


def run_worker_case(
    case: CaseSpec,
    *,
    packs_root: Path,
    audio_path: Path,
) -> dict[str, Any]:
    """Execute one real backend against one strictly verified local pack."""

    entry = MODEL_MANIFEST.get(case.model_key)
    if entry is None or entry.delivery != "native-download":
        raise NativeModelAcceptanceError("worker model authority is invalid")
    pack_root = (
        packs_root
        / cast(str, entry.package_bundle_id)
        / cast(str, entry.package_bundle_version)
    )
    pack_identity = verify_model_pack(pack_root, entry=entry)
    config = config_for_case(case, entry)
    expected_model_path = pack_root.joinpath(
        *PurePosixPath(cast(str, entry.package_relative_path)).parts
    )

    with _temporary_installed_pack_root(packs_root), deny_ip_network() as network_guard:
        if entry.role == "asr":
            from local_transcript_max.asr.faster_whisper_backend import (
                FasterWhisperBackend,
            )

            backend: Any = FasterWhisperBackend()
        elif entry.role == "asr-mlx":
            from local_transcript_max.asr.mlx_whisper_backend import MlxWhisperBackend

            backend = MlxWhisperBackend()
        else:  # pragma: no cover - native_download_entries enforces this
            raise NativeModelAcceptanceError("worker backend role is invalid")
        try:
            result = backend.transcribe(audio_path, config)
        finally:
            backend.clear_runtime_cache()
    if network_guard.denied_attempts:
        raise NativeModelAcceptanceError("backend attempted network access")
    if (
        result.model.model_id != entry.model_id
        or result.model.revision != entry.revision
        or result.model.revision_verified is not True
        or _absolute_path(Path(result.model.local_path)) != _absolute_path(expected_model_path)
    ):
        raise NativeModelAcceptanceError("backend result does not bind to the verified pack")
    nonempty_segments = [segment for segment in result.segments if segment.text.strip()]
    if not nonempty_segments:
        raise NativeModelAcceptanceError("real ASR inference returned no non-empty segment")
    return {
        "schema_version": WORKER_RESULT_SCHEMA_VERSION,
        "status": "pass",
        "case_id": case.case_id,
        "case_kind": case.case_kind,
        "preset": case.preset,
        "backend": config.asr.backend,
        "model_key": entry.key,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "pack_id": entry.package_bundle_id,
        "pack_version": entry.package_bundle_version,
        "pack_manifest_sha256": pack_identity["manifest_sha256"],
        "pack_payload_file_count": pack_identity["payload_file_count"],
        "pack_payload_size": pack_identity["payload_size"],
        "model_path_binding": "verified-pack-relative",
        "revision_verified": True,
        "segment_count": len(result.segments),
        "nonempty_segment_count": len(nonempty_segments),
        "word_count": sum(len(segment.words) for segment in result.segments),
        "network_attempts_denied": network_guard.denied_attempts,
        "transcript_withheld": True,
    }


def config_for_case(case: CaseSpec, entry: ModelEntry) -> PipelineConfig:
    if case.case_kind == "preset":
        if case.preset not in NORMAL_PRESET_MODELS:
            raise NativeModelAcceptanceError("worker preset is unknown")
        preset = cast(PresetName, case.preset)
        config = preset_config(preset)
        expected_key = NORMAL_PRESET_MODELS[preset]
        if config.asr.backend != "faster-whisper" or config.asr.model != expected_key:
            raise NativeModelAcceptanceError("effective preset mapping does not match authority")
        if expected_key != entry.key:
            raise NativeModelAcceptanceError("worker preset model key is inconsistent")
    else:
        config = PipelineConfig()
        if entry.role == "asr":
            config.asr.backend = "faster-whisper"
            config.asr.model = entry.key
            config.asr.device = "cpu"
            config.asr.compute_type = "int8_float32"
        elif entry.role == "asr-mlx":
            config.asr.backend = "mlx-whisper"
            config.asr.mlx_model = entry.key
            config.asr.mlx_fp16 = True
        else:
            raise NativeModelAcceptanceError("worker model role is invalid")
        config.asr.beam_size = 1
        config.asr.best_of = 1
        config.asr.temperature = 0.0
    config.offline = True
    config.asr.batched = False
    config.asr.vad_filter = False
    config.asr.word_timestamps = True
    config.asr.condition_on_previous_text = False
    config.asr.mlx_model_path = None
    return PipelineConfig.model_validate(config.model_dump(mode="python"))


@contextmanager
def deny_ip_network() -> Iterator[NetworkGuard]:
    """Deny Python IPv4/IPv6 resolution, connect, and datagram sends."""

    guard = NetworkGuard()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(instance: socket.socket, address: object) -> None:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            guard.reject()
        cast(Any, original_connect)(instance, address)

    def guarded_connect_ex(instance: socket.socket, address: object) -> int:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            guard.reject()
        return int(cast(Any, original_connect_ex)(instance, address))

    def guarded_sendto(instance: socket.socket, data: bytes, *args: object) -> int:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            guard.reject()
        return int(cast(Any, original_sendto)(instance, data, *args))

    def denied_create_connection(*_args: object, **_kwargs: object) -> socket.socket:
        guard.reject()

    def denied_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        guard.reject()

    socket_type = cast(Any, socket.socket)
    socket_module = cast(Any, socket)
    socket_type.connect = guarded_connect
    socket_type.connect_ex = guarded_connect_ex
    socket_type.sendto = guarded_sendto
    socket_module.create_connection = denied_create_connection
    socket_module.getaddrinfo = denied_getaddrinfo
    try:
        yield guard
    finally:
        socket_type.connect = original_connect
        socket_type.connect_ex = original_connect_ex
        socket_type.sendto = original_sendto
        socket_module.create_connection = original_create_connection
        socket_module.getaddrinfo = original_getaddrinfo


@contextmanager
def _temporary_installed_pack_root(packs_root: Path) -> Iterator[None]:
    """Bind normal installed-product resolution to one ephemeral verified root."""

    previous_root = model_cache_authority.INSTALLED_MODEL_PACKS_ROOT
    previous_environment = {
        name: os.environ.get(name)
        for name in (
            model_cache_authority.INSTALLED_PRODUCT_ENV,
            model_cache_authority.SYSTEM_MODEL_BUNDLE_ROOT_ENV,
            model_cache_authority.USER_MODEL_PACKS_ROOT_ENV,
        )
    }
    model_cache_authority.INSTALLED_MODEL_PACKS_ROOT = packs_root
    os.environ[model_cache_authority.INSTALLED_PRODUCT_ENV] = "1"
    os.environ[model_cache_authority.SYSTEM_MODEL_BUNDLE_ROOT_ENV] = os.fspath(
        model_cache_authority.INSTALLED_MODEL_BUNDLE_ROOT
    )
    os.environ[model_cache_authority.USER_MODEL_PACKS_ROOT_ENV] = os.fspath(packs_root)
    try:
        yield
    finally:
        model_cache_authority.INSTALLED_MODEL_PACKS_ROOT = previous_root
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build_acceptance_payload(
    *,
    fixture: Mapping[str, Any],
    pack_identities: Mapping[str, Mapping[str, Any]],
    model_results: Sequence[Mapping[str, Any]],
    preset_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(pack_identities) != 16 or len(model_results) != 16 or len(preset_results) != 4:
        raise NativeModelAcceptanceError("acceptance result does not close all required cases")
    payload = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "status": "pass",
        "catalog": {
            "schema_version": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
            "catalog_version": MODEL_DELIVERY_CATALOG_VERSION,
            "sha256": model_delivery_catalog_sha256(),
        },
        "pack_contract": {
            "schema_version": MODEL_PACK_SCHEMA_VERSION,
            "layout_version": MODEL_PACK_LAYOUT_VERSION,
        },
        "scope": {
            "native_pack_count": len(pack_identities),
            "native_model_inference_count": len(model_results),
            "normal_preset_inference_count": len(preset_results),
            "all_required_cases_closed": True,
        },
        "fixture": dict(fixture),
        "network_policy": {
            "python_ip_network": "denied",
            "upstream_offline_environment": True,
            "normal_runtime_downloads": False,
        },
        "models": list(model_results),
        "presets": list(preset_results),
        "privacy": {
            "local_paths_included": False,
            "transcript_included": False,
            "audio_hash_included": False,
            "credentials_included": False,
            "raw_backend_output_included": False,
        },
    }
    _assert_privacy_safe_payload(payload)
    return payload


def _read_worker_result(path: Path) -> dict[str, Any]:
    descriptor = _open_readonly_nofollow(path)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            content = handle.read(4 * 1024 * 1024 + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > 4 * 1024 * 1024:
        raise NativeModelAcceptanceError("worker result is unexpectedly large")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeModelAcceptanceError("worker result is malformed") from exc
    if not isinstance(payload, dict):
        raise NativeModelAcceptanceError("worker result has an invalid shape")
    _assert_privacy_safe_payload(payload)
    return cast(dict[str, Any], payload)


def _write_private_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    _assert_privacy_safe_payload(payload)
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = open_regular_nofollow(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_privacy_safe_payload(payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    lowered = encoded.casefold()
    if any(marker.casefold() in lowered for marker in _PRIVATE_TEXT_MARKERS):
        raise NativeModelAcceptanceError("acceptance JSON contains private or secret text")


def _case_kind_from_environment() -> CaseKind:
    value = _required_worker_environment("case_kind")
    if value not in {"model", "preset"}:
        raise NativeModelAcceptanceError("worker case kind is invalid")
    return cast(CaseKind, value)


def _required_worker_environment(key: str) -> str:
    value = os.environ.get(_WORKER_ENVIRONMENT_KEYS[key])
    if not value:
        raise NativeModelAcceptanceError(f"worker input is missing: {key}")
    return value


def _hash_regular_file(path: Path) -> dict[str, Any]:
    descriptor = _open_readonly_nofollow(path)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_nlink != 1:
            raise NativeModelAcceptanceError("model file is hardlinked")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, _READ_BLOCK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        final_metadata = os.fstat(descriptor)
        if _stat_identity(metadata) != _stat_identity(final_metadata):
            raise NativeModelAcceptanceError("model file changed during hashing")
        return {"size": size, "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise NativeModelAcceptanceError(f"{label} is missing or inaccessible") from exc


def _open_readonly_nofollow(path: Path) -> int:
    """Open one existing regular file without changing its mode."""

    candidate = reject_path_indirection(path)
    try:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise NativeModelAcceptanceError("private input file could not be opened") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise NativeModelAcceptanceError("private input is not a regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise NativeModelAcceptanceError("short write in private acceptance state")
        offset += written


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        action="append",
        type=Path,
        required=True,
        help=(
            "Explicit controlled local root. Repeat for separate CTranslate2/MLX "
            "sources. Every native model must resolve from exactly one root."
        ),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="Absent private work directory for packs and privacy-safe evidence.",
    )
    parser.add_argument(
        "--audio",
        type=Path,
        required=True,
        help="Bounded local PCM WAV used only inside private workers; transcript is discarded.",
    )
    parser.add_argument(
        "--license-directory",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "LICENSES",
    )
    parser.add_argument("--maximum-audio-seconds", type=float, default=15.0)
    parser.add_argument("--case-timeout-seconds", type=int, default=900)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None and sys.argv[1:] == [_WORKER_FLAG]:
        return worker_main()
    if argv is not None and list(argv) == [_WORKER_FLAG]:
        return worker_main()
    try:
        args = _parse_args(argv)
        if args.maximum_audio_seconds <= 0 or args.case_timeout_seconds <= 0:
            raise NativeModelAcceptanceError("acceptance bounds must be positive")
        work_dir = _absolute_path(args.work_dir)
        work_dir = create_acceptance_work_directory(work_dir)
        packs_root = ensure_private_directory(work_dir / "packs")
        transient_root = ensure_private_directory(work_dir / "transient")
        worker_root = ensure_private_directory(work_dir / "workers")
        fixture_path = work_dir / "fixture.wav"
        try:
            fixture = copy_and_describe_fixture(
                _absolute_path(args.audio),
                fixture_path,
                maximum_seconds=float(args.maximum_audio_seconds),
            )
            sources = resolve_source_models(
                [Path(root) for root in args.source_root],
            )
            pack_identities = assemble_verified_packs(
                sources,
                packs_root,
                transient_root,
                license_directory=_absolute_path(args.license_directory),
            )
            model_results, preset_results = run_inference_cases(
                acceptance_cases(),
                packs_root=packs_root,
                audio_path=fixture_path,
                worker_root=worker_root,
                timeout_seconds=int(args.case_timeout_seconds),
            )
            payload = build_acceptance_payload(
                fixture=fixture,
                pack_identities=pack_identities,
                model_results=model_results,
                preset_results=preset_results,
            )
            output = work_dir / DEFAULT_OUTPUT_FILENAME
            _write_private_json_exclusive(output, payload)
        finally:
            fixture_path.unlink(missing_ok=True)
            if os.path.lexists(transient_root):
                shutil.rmtree(transient_root)
            if os.path.lexists(worker_root):
                shutil.rmtree(worker_root)
    except Exception as exc:
        print(
            f"native model acceptance failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(
        "native_model_acceptance=pass "
        f"models={len(model_results)} presets={len(preset_results)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
