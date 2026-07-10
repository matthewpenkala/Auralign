# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed dataset acquisition policy and optional cloud mirror planning."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError

from .config import hf_token
from .errors import CommandExecutionError, LTMError
from .redaction import redact_obj, redact_text
from .utils import run_command, safe_repo_dir, which, write_json

DatasetProfile = Literal["runtime-samples"]
DatasetClass = Literal["sample", "cloud_only"]

DEFAULT_DATASET_PROFILE: DatasetProfile = "runtime-samples"
DEFAULT_MAX_LOCAL_GB = 65.0
DEFAULT_MIN_FREE_GB = 100.0
DEFAULT_SAMPLE_MAX_GB = 1.0
RUNTIME_MODEL_ESTIMATE_GB = 14.0


class DatasetPolicyError(LTMError):
    """Raised when a dataset operation violates the local-first policy."""


@dataclass(frozen=True)
class DatasetEntry:
    key: str
    repo_id: str
    repo_type: str
    classification: DatasetClass
    approx_size_gb: float
    license_notes: str
    access_notes: str
    description: str
    revision: str
    license_expression: str
    source_url: str
    terms_url: str
    attribution: str
    redistribution_status: str
    privacy_classification: str
    review_status: str
    reviewed_at: str
    acquisition_approved: bool = False
    mirror_approved: bool = False

    @property
    def directory_name(self) -> str:
        return safe_repo_dir(self.repo_id)

    def local_path(self, cache_dir: Path) -> Path:
        if self.classification == "sample":
            return cache_dir.expanduser() / "samples" / self.directory_name
        return cache_dir.expanduser() / "cloud_mirror" / self.directory_name


@dataclass(frozen=True)
class DatasetStatus:
    entry: DatasetEntry
    path: Path
    exists: bool
    local_status: str

    def to_record(self) -> dict[str, Any]:
        return {
            "key": self.entry.key,
            "repo_id": self.entry.repo_id,
            "repo_type": self.entry.repo_type,
            "classification": self.entry.classification,
            "approx_size_gb": self.entry.approx_size_gb,
            "license_notes": self.entry.license_notes,
            "access_notes": self.entry.access_notes,
            "description": self.entry.description,
            "revision": self.entry.revision,
            "license_expression": self.entry.license_expression,
            "source_url": self.entry.source_url,
            "terms_url": self.entry.terms_url,
            "attribution": self.entry.attribution,
            "redistribution_status": self.entry.redistribution_status,
            "privacy_classification": self.entry.privacy_classification,
            "compliance_review_status": self.entry.review_status,
            "compliance_reviewed_at": self.entry.reviewed_at,
            "acquisition_approved": self.entry.acquisition_approved,
            "mirror_approved": self.entry.mirror_approved,
            "local_path": str(self.path),
            "exists": self.exists,
            "local_status": self.local_status,
            "runtime_required": False,
        }


DATASET_MANIFEST: dict[str, DatasetEntry] = {
    "librispeech-dummy": DatasetEntry(
        key="librispeech-dummy",
        repo_id="hf-internal-testing/librispeech_asr_dummy",
        repo_type="dataset",
        classification="sample",
        approx_size_gb=0.01,
        license_notes="Tiny public Hugging Face test fixture for smoke tests.",
        access_notes="Public; no gated terms expected.",
        description="Tiny LibriSpeech-shaped sample set for bootstrap smoke checks.",
        revision="5be91486e11a2d616f4ec5db8d3fd248585ac07a",
        license_expression="NOASSERTION",
        source_url="https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy",
        terms_url="https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy",
        attribution="LibriSpeech-shaped test fixture; exact licensing provenance is unverified.",
        redistribution_status="blocked",
        privacy_classification="public_speech_sample_unverified_provenance",
        review_status="blocked_missing_publisher_license",
        reviewed_at="2026-07-10",
    ),
    "librispeech-full": DatasetEntry(
        key="librispeech-full",
        repo_id="openslr/librispeech_asr",
        repo_type="dataset",
        classification="cloud_only",
        approx_size_gb=124.0,
        license_notes="LibriSpeech is published under CC-BY-4.0; attribution applies.",
        access_notes="Large public research corpus; not downloaded to laptop by default.",
        description="Full LibriSpeech ASR corpus for research/evaluation, not runtime.",
        revision="71cacbfb7e2354c4226d01e70d77d5fca3d04ba1",
        license_expression="CC-BY-4.0",
        source_url="https://www.openslr.org/12",
        terms_url="https://www.openslr.org/12",
        attribution="LibriSpeech corpus, OpenSLR SLR12.",
        redistribution_status="permitted_with_attribution",
        privacy_classification="public_speech_corpus",
        review_status="verified",
        reviewed_at="2026-07-10",
        mirror_approved=True,
    ),
    "ami-full": DatasetEntry(
        key="ami-full",
        repo_id="edinburghcstr/ami",
        repo_type="dataset",
        classification="cloud_only",
        approx_size_gb=29.0,
        license_notes="AMI is published under CC-BY-4.0; attribution applies.",
        access_notes="Large public research corpus; not downloaded to laptop by default.",
        description="AMI meeting corpus for diarization research/evaluation, not runtime.",
        revision="46f28f2503e2ec48f8867a84eef356c70476beab",
        license_expression="CC-BY-4.0",
        source_url="https://groups.inf.ed.ac.uk/ami/corpus/",
        terms_url="https://groups.inf.ed.ac.uk/ami/corpus/",
        attribution="AMI Meeting Corpus.",
        redistribution_status="permitted_with_attribution",
        privacy_classification="public_meeting_speech_corpus",
        review_status="verified",
        reviewed_at="2026-07-10",
        mirror_approved=True,
    ),
    "voxceleb-full": DatasetEntry(
        key="voxceleb-full",
        repo_id="ProgramComputer/voxceleb",
        repo_type="dataset",
        classification="cloud_only",
        approx_size_gb=745.0,
        license_notes=(
            "Mirror metadata says CC-BY-4.0, but underlying audiovisual, voice, privacy, "
            "publicity, and biometric rights are not resolved by that tag."
        ),
        access_notes="Very large public research corpus; cloud mirror only by default.",
        description="VoxCeleb speaker corpus for research/evaluation, not runtime.",
        revision="b8a8825c87582831cc217b6ff84a36558ced84d6",
        license_expression="CC-BY-4.0",
        source_url="https://www.robots.ox.ac.uk/~vgg/data/voxceleb/",
        terms_url="https://huggingface.co/datasets/ProgramComputer/voxceleb",
        attribution="VoxCeleb research corpus; underlying recording rights remain separate.",
        redistribution_status="blocked_pending_rights_review",
        privacy_classification="biometric_identifiable_voice_high_risk",
        review_status="blocked_underlying_rights_and_biometric_review",
        reviewed_at="2026-07-10",
    ),
}


def dataset_entries(profile: DatasetProfile = DEFAULT_DATASET_PROFILE) -> list[DatasetEntry]:
    if profile != "runtime-samples":
        raise DatasetPolicyError("Only the runtime-samples dataset profile is supported.")
    return list(DATASET_MANIFEST.values())


def sample_entries(profile: DatasetProfile = DEFAULT_DATASET_PROFILE) -> list[DatasetEntry]:
    return [
        entry for entry in dataset_entries(profile) if entry.classification == "sample"
    ]


def cloud_only_entries(profile: DatasetProfile = DEFAULT_DATASET_PROFILE) -> list[DatasetEntry]:
    return [
        entry
        for entry in dataset_entries(profile)
        if entry.classification == "cloud_only"
    ]


def default_dataset_cache(cwd: Path | None = None) -> Path:
    return Path(os.getenv("LTM_DATASET_CACHE", str((cwd or Path.cwd()) / "datasets"))).expanduser()


def status_for_dataset(entry: DatasetEntry, cache_dir: Path) -> DatasetStatus:
    path = entry.local_path(cache_dir)
    exists = path.exists()
    if exists:
        local_status = "present"
    elif entry.classification == "cloud_only":
        local_status = "cloud_only"
    else:
        local_status = "missing"
    return DatasetStatus(entry=entry, path=path, exists=exists, local_status=local_status)


def dataset_audit_payload(
    cache_dir: Path,
    *,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
    remote_configured: bool = False,
) -> dict[str, Any]:
    statuses = [status_for_dataset(entry, cache_dir) for entry in dataset_entries(profile)]
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "dataset_cache": str(cache_dir.expanduser()),
                "profile": profile,
                "remote_configured": remote_configured,
                "summary": {
                    "total": len(statuses),
                    "sample_count": sum(
                        1 for status in statuses if status.entry.classification == "sample"
                    ),
                    "cloud_only_count": sum(
                        1
                        for status in statuses
                        if status.entry.classification == "cloud_only"
                    ),
                    "local_runtime_required": False,
                    "samples_present": all(
                        status.exists
                        for status in statuses
                        if status.entry.classification == "sample"
                    ),
                },
                "datasets": [status.to_record() for status in statuses],
            }
        ),
    )


def write_dataset_manifest(
    cache_dir: Path,
    *,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
    output_path: Path | None = None,
    remote_configured: bool = False,
) -> Path:
    output = output_path or cache_dir.expanduser() / "dataset_manifest.json"
    write_json(
        output,
        dataset_audit_payload(
            cache_dir,
            profile=profile,
            remote_configured=remote_configured,
        ),
    )
    return output


def dataset_budget_plan(
    cache_dir: Path,
    *,
    model_cache_dir: Path | None = None,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
    max_local_gb: float = DEFAULT_MAX_LOCAL_GB,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    allow_over_budget: bool = False,
) -> dict[str, Any]:
    approved_samples = [entry for entry in sample_entries(profile) if entry.acquisition_approved]
    sample_gb = sum(entry.approx_size_gb for entry in approved_samples)
    cloud_only_gb = sum(entry.approx_size_gb for entry in cloud_only_entries(profile))
    projected_local_gb = RUNTIME_MODEL_ESTIMATE_GB + sample_gb
    model_cache = model_cache_dir or cache_dir
    dataset_free_gb = _free_gb(cache_dir)
    model_free_gb = _free_gb(model_cache)
    same_volume = _same_volume(cache_dir, model_cache)
    free_gb = model_free_gb if same_volume else min(model_free_gb, dataset_free_gb)
    blockers: list[str] = []
    if projected_local_gb > max_local_gb:
        blockers.append(
            f"projected local footprint {projected_local_gb:.2f} GB exceeds "
            f"budget {max_local_gb:.2f} GB"
        )
    if same_volume and free_gb - projected_local_gb < min_free_gb:
        blockers.append(
            f"projected free space after bootstrap would be "
            f"{free_gb - projected_local_gb:.2f} GB, below reserve {min_free_gb:.2f} GB"
        )
    if not same_volume:
        projected_model_free_gb = model_free_gb - RUNTIME_MODEL_ESTIMATE_GB
        projected_dataset_free_gb = dataset_free_gb - sample_gb
        if projected_model_free_gb < min_free_gb:
            blockers.append(
                f"projected model-cache free space after bootstrap would be "
                f"{projected_model_free_gb:.2f} GB, below reserve {min_free_gb:.2f} GB"
            )
        if projected_dataset_free_gb < min_free_gb:
            blockers.append(
                f"projected dataset-cache free space after bootstrap would be "
                f"{projected_dataset_free_gb:.2f} GB, below reserve {min_free_gb:.2f} GB"
            )
    ok = not blockers or allow_over_budget
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "profile": profile,
                "ok": ok,
                "allow_over_budget": allow_over_budget,
                "max_local_gb": max_local_gb,
                "min_free_gb": min_free_gb,
                "free_gb": round(free_gb, 2),
                "model_cache": str(model_cache.expanduser()),
                "dataset_cache": str(cache_dir.expanduser()),
                "model_free_gb": round(model_free_gb, 2),
                "dataset_free_gb": round(dataset_free_gb, 2),
                "model_and_dataset_cache_same_volume": same_volume,
                "projected_local_gb": round(projected_local_gb, 2),
                "runtime_model_estimate_gb": RUNTIME_MODEL_ESTIMATE_GB,
                "sample_dataset_estimate_gb": round(sample_gb, 2),
                "cloud_only_dataset_estimate_gb": round(cloud_only_gb, 2),
                "blockers": blockers,
                "local_downloads": [
                    entry.key for entry in approved_samples
                ],
                "blocked_local_downloads": [
                    entry.key for entry in sample_entries(profile) if not entry.acquisition_approved
                ],
                "cloud_only": [
                    entry.key for entry in cloud_only_entries(profile)
                ],
            }
        ),
    )


def pull_sample_datasets(
    cache_dir: Path,
    *,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
    max_sample_gb: float = DEFAULT_SAMPLE_MAX_GB,
) -> dict[str, Any]:
    cache_dir = cache_dir.expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    gated_stub: list[dict[str, str]] = []

    for entry in sample_entries(profile):
        local_dir = entry.local_path(cache_dir)
        if not entry.acquisition_approved:
            reason = f"compliance approval required: {entry.review_status}"
            results.append(_dataset_result(entry, local_dir, "blocked", reason))
            gated_stub.append(
                {"repo_id": entry.repo_id, "repo_type": entry.repo_type, "reason": reason}
            )
            continue
        if entry.approx_size_gb > max_sample_gb:
            reason = "sample exceeds configured sample budget"
            results.append(_dataset_result(entry, local_dir, "skipped", reason))
            gated_stub.append(
                {"repo_id": entry.repo_id, "repo_type": entry.repo_type, "reason": reason}
            )
            continue
        try:
            snapshot_download(
                repo_id=entry.repo_id,
                repo_type=entry.repo_type,
                revision=entry.revision,
                local_dir=local_dir,
                local_dir_use_symlinks=False,
                token=hf_token(),
            )
            results.append(_dataset_result(entry, local_dir, "fetched", None))
        except (GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError) as exc:
            reason = exc.__class__.__name__
            results.append(_dataset_result(entry, local_dir, "skipped", reason))
            gated_stub.append(
                {"repo_id": entry.repo_id, "repo_type": entry.repo_type, "reason": reason}
            )
        except Exception as exc:  # pragma: no cover - defensive around network stack
            reason = exc.__class__.__name__
            results.append(_dataset_result(entry, local_dir, "skipped", reason))
            gated_stub.append(
                {"repo_id": entry.repo_id, "repo_type": entry.repo_type, "reason": reason}
            )

    write_json(cache_dir / "sample_pull_results.json", redact_obj({"results": results}))
    write_json(cache_dir / "gated_stub.json", redact_obj(gated_stub))
    manifest_path = write_dataset_manifest(cache_dir, profile=profile)
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "profile": profile,
                "dataset_cache": str(cache_dir),
                "manifest_path": str(manifest_path),
                "results": results,
                "gated_stub_path": str(cache_dir / "gated_stub.json"),
            }
        ),
    )


def cloud_plan(
    cache_dir: Path,
    *,
    remote: str | None = None,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
) -> dict[str, Any]:
    remote_value = _validated_remote(remote or os.getenv("LTM_DATASET_MIRROR_REMOTE"))
    plans = []
    blocked = []
    for entry in cloud_only_entries(profile):
        if not entry.mirror_approved:
            blocked.append(
                {
                    "key": entry.key,
                    "repo_id": entry.repo_id,
                    "reason": entry.review_status,
                }
            )
            continue
        local_dir = entry.local_path(cache_dir)
        remote_dir = f"{remote_value.rstrip('/')}/{entry.directory_name}"
        plans.append(
            {
                "key": entry.key,
                "repo_id": entry.repo_id,
                "approx_size_gb": entry.approx_size_gb,
                "classification": entry.classification,
                "revision": entry.revision,
                "license_expression": entry.license_expression,
                "local_path": str(local_dir),
                "remote_path": remote_dir,
                "hydrate_command": (
                    "huggingface-cli download "
                    f"{entry.repo_id} --repo-type {entry.repo_type} "
                    f"--revision {entry.revision} --local-dir {local_dir}"
                ),
                "upload_command": (
                    f"rclone copy {local_dir} {remote_dir} --progress --checksum"
                ),
            }
        )
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "profile": profile,
                "remote": remote_value,
                "requires_explicit_bulk_transfer_approval": True,
                "runtime_dependency": False,
                "plans": plans,
                "blocked": blocked,
            }
        ),
    )


def cloud_sync(
    cache_dir: Path,
    *,
    remote: str | None = None,
    yes_i_understand_bulk_transfer: bool = False,
    profile: DatasetProfile = DEFAULT_DATASET_PROFILE,
) -> dict[str, Any]:
    if not yes_i_understand_bulk_transfer:
        raise DatasetPolicyError(
            "Refusing bulk dataset mirror sync without --yes-i-understand-bulk-transfer."
        )
    if not which("rclone"):
        raise DatasetPolicyError("rclone is required for cloud dataset sync but is not on PATH.")
    plan = cloud_plan(cache_dir, remote=remote, profile=profile)
    synced: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in plan["plans"]:
        local_path = Path(str(item["local_path"]))
        if not local_path.exists():
            skipped.append({"key": str(item["key"]), "reason": "local cloud mirror path missing"})
            continue
        try:
            run_command(
                [
                    "rclone",
                    "copy",
                    str(local_path),
                    str(item["remote_path"]),
                    "--progress",
                    "--checksum",
                ]
            )
            synced.append({"key": str(item["key"]), "remote_path": str(item["remote_path"])})
        except CommandExecutionError as exc:
            raise DatasetPolicyError(str(exc)) from exc
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "profile": profile,
                "synced": synced,
                "skipped": skipped,
                "runtime_dependency": False,
            }
        ),
    )


def _dataset_result(
    entry: DatasetEntry,
    path: Path,
    status: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "key": entry.key,
        "repo_id": entry.repo_id,
        "repo_type": entry.repo_type,
        "classification": entry.classification,
        "local_path": str(path),
        "status": status,
        "reason": reason,
    }


def _free_gb(path: Path) -> float:
    anchor = _nearest_existing(path.expanduser())
    usage = shutil.disk_usage(anchor)
    return usage.free / (1024**3)


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    if current.exists():
        return current
    return Path.cwd()


def _same_volume(first: Path, second: Path) -> bool:
    first_anchor = _nearest_existing(first.expanduser())
    second_anchor = _nearest_existing(second.expanduser())
    try:
        return os.stat(first_anchor).st_dev == os.stat(second_anchor).st_dev
    except OSError:
        return first_anchor == second_anchor


def _validated_remote(remote: str | None) -> str:
    if not remote:
        raise DatasetPolicyError(
            "Set LTM_DATASET_MIRROR_REMOTE or pass --remote before planning cloud mirrors."
        )
    value = remote.strip()
    if not value:
        raise DatasetPolicyError("Dataset mirror remote cannot be empty.")
    lowered = value.lower()
    unsafe_markers = ("token=", "secret=", "password=", "apikey=", "api_key=", "://", "@")
    if any(marker in lowered for marker in unsafe_markers):
        raise DatasetPolicyError(
            "Refusing remote value that appears to contain inline credentials. "
            "Use a named rclone remote such as 'gdrive:auralign-datasets'."
        )
    return redact_text(value)
