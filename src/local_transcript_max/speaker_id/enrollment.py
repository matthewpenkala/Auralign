# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Local speaker enrollment, profile management, and speaker-map matching."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from local_transcript_max.platform_security import atomic_write_text, ensure_private_directory
from local_transcript_max.redaction import redact_obj
from local_transcript_max.schemas import SpeakerEnrollment, SpeakerMap, Transcript
from local_transcript_max.utils import read_json, sha256_file, write_json

from .embeddings import extract_embedding, load_embedding, save_embedding
from .matching import cosine_similarity, validate_embedding

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
PRODUCTION_EMBEDDING_BACKEND = "speechbrain-ecapa"
PROFILE_EXPORT_SCHEMA = "speaker_profiles.v1"
RENAME_RECORD_SCHEMA = "speaker_rename.v1"
MAX_RENAME_RECORDS = 1024
RENAME_CONTEXT_ARTIFACTS = (
    "transcript.json",
    "run_metadata.json",
    "speaker_report.json",
    "speaker_validation_report.json",
)
ANONYMOUS_SPEAKER_RE = re.compile(r"^SPEAKER_\d+$", re.IGNORECASE)
LegacyProfileReason = Literal[
    "legacy_profile_backend_waveform_stats",
    "legacy_profile_backend_mock",
    "legacy_profile_backend_unknown",
    "legacy_profile_backend_mismatch",
]


@dataclass(frozen=True, slots=True)
class ValidatedRenameEvidence:
    """One explicit rename proven to belong to the current job artifacts."""

    speaker: str
    name: str
    job_fingerprint: str
    artifact_hashes: dict[str, str]
    record_id: str
    record_hash: str


def speaker_home() -> Path:
    root = Path(
        os.getenv("LTM_SPEAKER_PROFILE_DIR", "~/.local-transcript-max/speakers")
    ).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if root.is_symlink():
        raise ValueError("Speaker profile root cannot be a symlink.")
    return root


def rename_home() -> Path:
    root = Path(os.getenv("LTM_SPEAKER_RENAME_DIR", "~/.local-transcript-max/renames")).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if root.is_symlink():
        raise ValueError("Speaker rename root cannot be a symlink.")
    return root


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "speaker"
    if len(normalized) <= 96:
        return normalized
    return f"{normalized[:79]}-{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _ensure_private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Private speaker storage cannot use symlink directories.")
    try:
        return ensure_private_directory(path)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Private speaker storage must be a regular directory.") from exc


def _assert_contained(path: Path, root: Path, *, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_parent = path.parent.resolve()
    if resolved_parent != resolved_root and not resolved_parent.is_relative_to(resolved_root):
        raise ValueError(f"{label} escapes the configured speaker profile root.")
    if path.is_symlink():
        raise ValueError(f"{label} cannot be a symlink.")
    return path


def _profile_dir(name: str, *, detect_collision: bool = True) -> Path:
    root = speaker_home()
    path = root / _slug(name)
    _assert_contained(path, root, label="Speaker profile directory")
    if path.is_symlink():
        raise ValueError("Speaker profile directories cannot be symlinks.")
    metadata = path / "profile.json"
    if detect_collision and metadata.exists():
        if metadata.is_symlink() or not metadata.is_file():
            raise ValueError("Speaker profile metadata must be a regular file.")
        existing = SpeakerEnrollment.model_validate_json(metadata.read_text(encoding="utf-8"))
        if existing.name != name:
            raise ValueError(
                f"Speaker names '{existing.name}' and '{name}' collide after slug normalization."
            )
    return path


def _private_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Private speaker files cannot be symlinks.")
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        atomic_write_text(path, encoded)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Private speaker file publication failed.") from exc


def _profile_backend_reason(backend: str) -> LegacyProfileReason:
    normalized = backend.strip().lower()
    if normalized == "waveform-stats":
        return "legacy_profile_backend_waveform_stats"
    if normalized == "mock":
        return "legacy_profile_backend_mock"
    return "legacy_profile_backend_unknown"


def _unit_interval(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number in [0, 1].")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{label} must be finite and in [0, 1].")
    return normalized


def enroll_speaker(
    name: str,
    audio_path: Path | None = None,
    *,
    audio_dir: Path | None = None,
    allow_fallback: bool = False,
) -> SpeakerEnrollment:
    samples = enrollment_samples(audio_path, audio_dir)
    root = _ensure_private_dir(speaker_home())
    profile_dir = _profile_dir(name)
    _ensure_private_dir(profile_dir)
    vectors: list[list[float]] = []
    backend_names: list[str] = []
    sample_root = profile_dir / "samples"
    _assert_contained(sample_root, root, label="Speaker sample directory")
    _ensure_private_dir(sample_root)
    copied_samples: list[str] = []
    for sample in samples:
        if sample.is_symlink() or not sample.is_file():
            raise ValueError("Enrollment samples must be regular files, not symlinks.")
        backend, vector = extract_embedding(sample, allow_fallback=allow_fallback)
        backend_names.append(backend)
        vectors.append(validate_embedding(vector, label="Enrollment embedding"))
        copied = sample_root / sample.name
        _assert_contained(copied, root, label="Speaker sample")
        if sample.resolve() != copied.resolve():
            if copied.is_symlink():
                raise ValueError("Enrollment sample destinations cannot be symlinks.")
            shutil.copy2(sample, copied)
            copied.chmod(0o600)
        copied_samples.append(str(copied))
    backend = backend_names[0]
    if any(candidate != backend for candidate in backend_names):
        raise ValueError("Enrollment samples produced incompatible embedding backends.")
    embedding = average_vectors(vectors)
    embedding_path = profile_dir / "embedding.json"
    _assert_contained(embedding_path, root, label="Speaker embedding")
    save_embedding(embedding_path, backend, embedding)
    enrollment = SpeakerEnrollment(
        name=name,
        embedding_path=str(embedding_path),
        sample_paths=copied_samples,
        backend=backend,
    )
    metadata_path = profile_metadata_path(name)
    _private_write_json(metadata_path, enrollment.model_dump(mode="json"))
    write_profiles_index()
    return enrollment


def enrollment_samples(audio_path: Path | None, audio_dir: Path | None) -> list[Path]:
    samples: list[Path] = []
    if audio_path is not None:
        samples.append(audio_path.expanduser())
    if audio_dir is not None:
        root = audio_dir.expanduser()
        samples.extend(
            path
            for path in sorted(root.iterdir())
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )
    if not samples:
        raise ValueError("Provide --audio or --audio-dir for enrollment.")
    return samples


def list_enrollments(profile_dir: Path | None = None) -> list[SpeakerEnrollment]:
    root = profile_dir.expanduser() if profile_dir is not None else speaker_home()
    if not root.is_absolute():
        root = Path.cwd() / root
    if root.is_symlink():
        raise ValueError("Speaker profile root cannot be a symlink.")
    if not root.exists():
        return []
    _ensure_private_dir(root)
    enrollments: list[SpeakerEnrollment] = []
    for path in sorted(root.glob("*/profile.json")):
        _assert_contained(path, root, label="Speaker profile metadata")
        if path.parent.is_symlink() or not path.is_file():
            raise ValueError("Speaker profile metadata must be a regular contained file.")
        path.parent.chmod(0o700)
        path.chmod(0o600)
        profile = SpeakerEnrollment.model_validate_json(path.read_text(encoding="utf-8"))
        if path.parent.name != _slug(profile.name):
            raise ValueError("Speaker profile directory does not match its normalized name.")
        enrollments.append(profile)
    return enrollments


def inspect_speaker(name: str) -> SpeakerEnrollment:
    path = profile_metadata_path(name)
    if not path.exists():
        raise FileNotFoundError(f"No speaker profile named '{name}'.")
    root = speaker_home()
    _assert_contained(path, root, label="Speaker profile metadata")
    if path.parent.is_symlink() or not path.is_file():
        raise ValueError("Speaker profile metadata must be a regular contained file.")
    return SpeakerEnrollment.model_validate_json(path.read_text(encoding="utf-8"))


def delete_speaker(name: str, *, yes: bool = False) -> Path:
    if not yes:
        raise ValueError("Speaker deletion requires --yes.")
    path = _profile_dir(name)
    if not path.exists():
        raise FileNotFoundError(f"No speaker profile named '{name}'.")
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Speaker profile must be a regular directory.")
    shutil.rmtree(path)
    write_profiles_index()
    return path


def rename_speaker(
    job_id: str,
    speaker: str,
    name: str,
    *,
    job_dir: Path | None = None,
) -> Path:
    """Record a legacy explicit rename without mutating the job.

    The existing three-argument call remains record-only.  Passing ``job_dir``
    enriches the record with canonical artifact hashes so the resolver can
    later treat it as safe evidence.  Unbound historical records remain
    readable and editable but are intentionally not apply-safe evidence.
    """

    if not job_id.strip() or not speaker.strip() or not name.strip():
        raise ValueError("Job ID, speaker label, and display name must not be empty.")
    context_dir = job_dir
    if context_dir is None:
        job_id_path = Path(job_id).expanduser()
        if job_id_path.is_dir():
            context_dir = job_id_path
    root = _ensure_private_dir(rename_home())
    path = root / f"{_slug(job_id)}.json"
    _assert_contained(path, root, label="Speaker rename record")
    job_id_hash = sha256(job_id.encode("utf-8")).hexdigest()
    mappings: dict[str, str] = {}
    payload: dict[str, Any]
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Speaker rename record must be a regular file.")
        existing = read_json(path)
        if not isinstance(existing, dict):
            raise ValueError("Speaker rename record must contain a JSON object.")
        if existing.get("schema_version") == RENAME_RECORD_SCHEMA:
            existing_hash = existing.get("job_id_hash")
            if existing_hash != job_id_hash:
                raise ValueError(
                    "Job IDs collide after slug normalization; choose a distinct record key."
                )
            raw_mappings = existing.get("mappings")
            if not isinstance(raw_mappings, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_mappings.items()
            ):
                raise ValueError("Speaker rename mappings are malformed.")
            mappings = dict(raw_mappings)
            payload = dict(existing)
        elif _is_legacy_rename_payload(existing):
            # Preserve the old record-only shape unless the caller supplies a
            # job directory and therefore enough evidence to bind/migrate it.
            if context_dir is None:
                existing[speaker] = name
                _private_write_json(path, existing)
                return path
            mappings = {str(key): str(value) for key, value in existing.items()}
            payload = {
                "schema_version": RENAME_RECORD_SCHEMA,
                "job_id_hash": job_id_hash,
                "mappings": mappings,
                "context": None,
            }
        else:
            raise ValueError("Unsupported speaker rename record schema.")
    else:
        payload = {
            "schema_version": RENAME_RECORD_SCHEMA,
            "job_id_hash": job_id_hash,
            "mappings": mappings,
            "context": None,
        }
    mappings[speaker] = name
    payload["mappings"] = mappings
    if context_dir is not None:
        payload["context"] = _build_rename_context(context_dir)
    _private_write_json(path, payload)
    return path


def export_profiles(output: Path) -> Path:
    output = output.expanduser()
    profiles: list[dict[str, Any]] = []
    for profile in list_enrollments():
        backend, vector = _load_profile_embedding(profile)
        profiles.append(
            {
                "name": profile.name,
                "backend": backend,
                "embedding": vector,
                "created_at": profile.created_at.isoformat(),
            }
        )
    _private_write_json(
        output,
        {"schema_version": PROFILE_EXPORT_SCHEMA, "profiles": profiles},
    )
    return output


def import_profiles(input_path: Path) -> int:
    payload = read_json(input_path.expanduser())
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), list):
        raise ValueError("Speaker profile import must contain a profiles array.")
    schema_version = payload.get("schema_version")
    if schema_version not in {None, PROFILE_EXPORT_SCHEMA}:
        raise ValueError(f"Unsupported speaker profile schema: {schema_version}")

    validated: list[tuple[SpeakerEnrollment, str, list[float]]] = []
    seen_slugs: dict[str, str] = {}
    for raw_row in payload["profiles"]:
        if not isinstance(raw_row, dict):
            raise ValueError("Every imported speaker profile must be a JSON object.")
        if "embedding" in raw_row:
            profile, backend, vector = _validate_portable_profile(raw_row)
        else:
            profile, backend, vector = _validate_legacy_profile(raw_row)
        slug = _slug(profile.name)
        previous_name = seen_slugs.get(slug)
        if previous_name is not None and previous_name != profile.name:
            raise ValueError(
                f"Speaker names '{previous_name}' and '{profile.name}' "
                "collide after slug normalization."
            )
        seen_slugs[slug] = profile.name
        _profile_dir(profile.name)
        validated.append((profile, backend, vector))

    _ensure_private_dir(speaker_home())
    for profile, backend, vector in validated:
        profile_dir = _profile_dir(profile.name)
        _ensure_private_dir(profile_dir)
        embedding_path = profile_dir / "embedding.json"
        _assert_contained(embedding_path, speaker_home(), label="Speaker embedding")
        save_embedding(embedding_path, backend, vector)
        imported = profile.model_copy(
            update={
                "embedding_path": str(embedding_path),
                # Portable exports intentionally omit source sample paths.
                "sample_paths": [],
                "backend": backend,
            }
        )
        _private_write_json(profile_dir / "profile.json", imported.model_dump(mode="json"))
    write_profiles_index()
    return len(validated)


def _load_profile_embedding(profile: SpeakerEnrollment) -> tuple[str, list[float]]:
    root = speaker_home()
    profile_dir = _profile_dir(profile.name)
    canonical = profile_dir / "embedding.json"
    _assert_contained(canonical, root, label="Speaker embedding")
    if profile_dir.is_symlink() or canonical.is_symlink() or not canonical.is_file():
        raise ValueError(f"Speaker profile '{profile.name}' has no safe local embedding.")
    profile_dir.chmod(0o700)
    canonical.chmod(0o600)

    recorded = Path(profile.embedding_path).expanduser()
    if recorded.is_absolute():
        recorded_candidate = recorded
    else:
        recorded_candidate = profile_dir / recorded
    if recorded_candidate.is_symlink() or recorded_candidate.resolve() != canonical.resolve():
        raise ValueError(
            f"Speaker profile '{profile.name}' references an embedding outside its profile."
        )
    return load_embedding(canonical)


def _validate_portable_profile(
    row: dict[str, Any],
) -> tuple[SpeakerEnrollment, str, list[float]]:
    name = row.get("name")
    backend = row.get("backend")
    raw_vector = row.get("embedding")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Imported profile name must be a non-empty string.")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("Imported profile backend must be a non-empty string.")
    if not isinstance(raw_vector, list):
        raise ValueError("Imported profile embedding must be an array.")
    vector = validate_embedding(raw_vector, label=f"Imported embedding for {name}")
    profile_data: dict[str, Any] = {
        "name": name,
        "embedding_path": "embedding.json",
        "sample_paths": [],
        "backend": backend,
    }
    if "created_at" in row:
        profile_data["created_at"] = row["created_at"]
    profile = SpeakerEnrollment.model_validate(profile_data)
    return profile, backend, vector


def _validate_legacy_profile(
    row: dict[str, Any],
) -> tuple[SpeakerEnrollment, str, list[float]]:
    """Read old metadata without ever dereferencing its imported path."""

    profile = SpeakerEnrollment.model_validate(row)
    canonical = _profile_dir(profile.name) / "embedding.json"
    _assert_contained(canonical, speaker_home(), label="Speaker embedding")
    if canonical.is_symlink() or not canonical.is_file():
        raise ValueError(
            "Legacy profile metadata is not portable and no safe local embedding exists. "
            "Re-export the profile with the current Auralign version."
        )
    backend, vector = load_embedding(canonical)
    if profile.backend != backend:
        raise ValueError("Legacy profile backend does not match its safe local embedding.")
    return profile, backend, vector


def _is_legacy_rename_payload(payload: dict[str, Any]) -> bool:
    return "schema_version" not in payload and all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    )


def _safe_rename_mapping(speaker: str, name: str) -> bool:
    if not ANONYMOUS_SPEAKER_RE.fullmatch(speaker) or not 1 <= len(name) <= 80:
        return False
    if ANONYMOUS_SPEAKER_RE.fullmatch(name) or name.casefold() in {
        "unknown",
        "unattributed",
        "null",
        "none",
    }:
        return False
    return all(
        not unicodedata.category(character).startswith("C") and character not in "\r\n"
        for character in name
    )


def _context_fingerprint(artifact_hashes: dict[str, str]) -> str:
    encoded = json.dumps(artifact_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _build_rename_context(job_dir: Path) -> dict[str, Any]:
    expanded = job_dir.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise ValueError("Rename job context must be a regular local directory.")
    artifact_hashes: dict[str, str] = {}
    for name in RENAME_CONTEXT_ARTIFACTS:
        path = expanded / name
        if path.is_symlink():
            raise ValueError("Rename context artifacts cannot be symlinks.")
        if path.exists():
            if not path.is_file():
                raise ValueError("Rename context artifacts must be regular files.")
            artifact_hashes[name] = sha256_file(path)
    if "transcript.json" not in artifact_hashes:
        raise ValueError("Hash-bound rename evidence requires transcript.json.")
    return {
        "schema_version": "speaker_rename_context.v1",
        "job_fingerprint": _context_fingerprint(artifact_hashes),
        "artifact_hashes": artifact_hashes,
    }


def _validated_rename_record(
    path: Path,
    job_dir: Path,
    *,
    job_id_hash: str | None,
) -> list[ValidatedRenameEvidence]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        return []
    try:
        payload = read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("schema_version") != RENAME_RECORD_SCHEMA:
        # Flat historical records are deliberately readable by rename_speaker,
        # but have no current-job hash binding and therefore are not evidence.
        return []
    if job_id_hash is not None and payload.get("job_id_hash") != job_id_hash:
        return []
    context = payload.get("context")
    mappings = payload.get("mappings")
    if not isinstance(context, dict) or not isinstance(mappings, dict):
        return []
    raw_hashes = context.get("artifact_hashes")
    fingerprint = context.get("job_fingerprint")
    if (
        context.get("schema_version") != "speaker_rename_context.v1"
        or not isinstance(raw_hashes, dict)
        or not isinstance(fingerprint, str)
        or "transcript.json" not in raw_hashes
    ):
        return []
    artifact_hashes: dict[str, str] = {}
    for name, digest in raw_hashes.items():
        if (
            name not in RENAME_CONTEXT_ARTIFACTS
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return []
        artifact = job_dir / name
        if artifact.is_symlink() or not artifact.is_file():
            return []
        if sha256_file(artifact) != digest:
            return []
        artifact_hashes[name] = digest
    if _context_fingerprint(artifact_hashes) != fingerprint:
        return []
    record_hash = sha256_file(path)
    record_id = f"rename:{record_hash[:16]}"
    return [
        ValidatedRenameEvidence(
            speaker=str(speaker),
            name=str(name),
            job_fingerprint=fingerprint,
            artifact_hashes=dict(artifact_hashes),
            record_id=record_id,
            record_hash=record_hash,
        )
        for speaker, name in sorted(mappings.items())
        if isinstance(speaker, str)
        and isinstance(name, str)
        and _safe_rename_mapping(speaker, name)
    ]


def load_validated_rename_evidence(
    job_dir: Path,
    *,
    job_id: str | None = None,
) -> list[ValidatedRenameEvidence]:
    """Load only collision-safe legacy renames bound to current job hashes."""

    expanded_job = job_dir.expanduser()
    if expanded_job.is_symlink() or not expanded_job.is_dir():
        raise ValueError("Rename evidence job must be a regular local directory.")
    root = rename_home()
    if not root.exists():
        return []
    _ensure_private_dir(root)
    records = sorted(root.glob("*.json"))
    if len(records) > MAX_RENAME_RECORDS:
        raise ValueError(f"Rename record count exceeds the {MAX_RENAME_RECORDS}-file limit.")
    expected_job_hash = sha256(job_id.encode("utf-8")).hexdigest() if job_id is not None else None
    candidates: list[ValidatedRenameEvidence] = []
    for path in records:
        _assert_contained(path, root, label="Speaker rename record")
        candidates.extend(
            _validated_rename_record(path, expanded_job, job_id_hash=expected_job_hash)
        )

    # Multiple bound records for the same speaker are safe only when they
    # agree.  Conflicting explicit names must reach review, not auto-apply.
    grouped: dict[str, list[ValidatedRenameEvidence]] = {}
    for evidence in candidates:
        grouped.setdefault(evidence.speaker, []).append(evidence)
    validated: list[ValidatedRenameEvidence] = []
    for speaker in sorted(grouped):
        rows = grouped[speaker]
        if len({row.name for row in rows}) != 1:
            continue
        validated.append(sorted(rows, key=lambda row: row.record_id)[0])
    return validated


def match_job_speakers(
    job_dir: Path,
    *,
    threshold: float = 0.75,
    ambiguity_margin: float = 0.05,
    profile_dir: Path | None = None,
) -> dict[str, Any]:
    threshold = _unit_interval(threshold, label="Speaker match threshold")
    ambiguity_margin = _unit_interval(ambiguity_margin, label="Speaker ambiguity margin")
    profiles = load_profile_embeddings(profile_dir)
    transcript_path = job_dir / "transcript.json"
    transcript = (
        Transcript.model_validate(read_json(transcript_path))
        if transcript_path.exists()
        else Transcript(input_path=str(job_dir))
    )
    cluster_embeddings_path = job_dir / "speaker_cluster_embeddings.json"
    cluster_embeddings = (
        read_json(cluster_embeddings_path) if cluster_embeddings_path.exists() else {}
    )
    speakers = sorted(
        {turn.speaker for turn in transcript.diarization_turns}
        | {word.speaker for word in transcript.words if word.speaker}
    )
    mappings = []
    for speaker in speakers:
        vector = cluster_embeddings.get(speaker)
        if vector is None:
            mappings.append(unknown_mapping(speaker, "missing_cluster_embedding"))
            continue
        alternatives = score_profiles(vector, profiles)
        if not alternatives:
            non_evidence_reasons = sorted(
                {
                    str(profile["evidence_reason"])
                    for profile in profiles.values()
                    if not profile["evidence_eligible"]
                }
            )
            mappings.append(
                unknown_mapping(
                    speaker,
                    (
                        "local_profile_backend_unavailable"
                        if non_evidence_reasons
                        else "no_enrolled_profiles"
                    ),
                    non_evidence_reasons=non_evidence_reasons,
                )
            )
            continue
        best = alternatives[0]
        second = alternatives[1] if len(alternatives) > 1 else None
        ambiguous = second is not None and best["score"] - second["score"] < ambiguity_margin
        if best["score"] < threshold or ambiguous:
            mappings.append(
                {
                    "speaker": speaker,
                    "label": "UNKNOWN",
                    "confidence": best["score"],
                    "alternatives": alternatives[:3],
                    "ambiguous": ambiguous,
                    "source_profile_id": None,
                    "profile_backend": best["backend"],
                    "reason": "below_threshold_or_ambiguous",
                }
            )
            continue
        mappings.append(
            {
                "speaker": speaker,
                "label": best["name"],
                "confidence": best["score"],
                "alternatives": alternatives[:3],
                "ambiguous": False,
                "source_profile_id": best["profile_id"],
                "profile_backend": best["backend"],
                "reason": "conservative_embedding_match",
            }
        )
    payload = cast(
        dict[str, Any],
        redact_obj(
            {
                "schema_version": "speaker_map.v2",
                "transcript_sha256": sha256_file(transcript_path)
                if transcript_path.exists()
                else None,
                "cluster_embeddings_sha256": sha256_file(cluster_embeddings_path)
                if cluster_embeddings_path.exists()
                else None,
                "mappings": mappings,
                "threshold": threshold,
                "ambiguity_margin": ambiguity_margin,
            }
        ),
    )
    write_json(job_dir / "speaker_map.json", payload)
    if transcript_path.exists():
        matched = apply_speaker_matches(transcript, mappings)
        write_json(job_dir / "transcript.speaker_matched.json", matched.model_dump(mode="json"))
    return payload


def load_profile_embeddings(profile_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for profile in list_enrollments(profile_dir):
        backend, vector = _load_profile_embedding(profile)
        profile_id = _slug(profile.name)
        eligible = (
            profile.backend == PRODUCTION_EMBEDDING_BACKEND
            and backend == PRODUCTION_EMBEDDING_BACKEND
        )
        if profile.backend != backend:
            reason: LegacyProfileReason | None = "legacy_profile_backend_mismatch"
        elif not eligible:
            reason = _profile_backend_reason(backend)
        else:
            reason = None
        profiles[profile_id] = {
            "name": profile.name,
            "profile_id": profile_id,
            "backend": backend,
            "embedding": vector,
            "evidence_eligible": eligible,
            "evidence_reason": reason,
        }
    return profiles


def score_profiles(
    vector: list[float],
    profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    validated = validate_embedding(vector, label="Cluster embedding")
    scored: list[dict[str, Any]] = []
    for profile in profiles.values():
        if not profile["evidence_eligible"]:
            continue
        similarity = cosine_similarity(validated, profile["embedding"])
        scored.append(
            {
                "name": profile["name"],
                "profile_id": profile["profile_id"],
                # Match confidence is represented on [0, 1], while the lower
                # level cosine helper retains its mathematical [-1, 1] range.
                "score": round(max(0.0, similarity), 6),
                "backend": profile["backend"],
            }
        )
    return sorted(scored, key=lambda row: (-row["score"], row["profile_id"]))


def unknown_mapping(
    speaker: str,
    reason: str,
    *,
    non_evidence_reasons: list[str] | None = None,
) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "speaker": speaker,
        "label": "UNKNOWN",
        "confidence": None,
        "alternatives": [],
        "ambiguous": True,
        "source_profile_id": None,
        "reason": reason,
    }
    if non_evidence_reasons:
        mapping["non_evidence_reasons"] = non_evidence_reasons
    return mapping


def apply_speaker_matches(transcript: Transcript, mappings: list[dict[str, Any]]) -> Transcript:
    by_speaker = {str(row["speaker"]): row for row in mappings}
    speaker_maps = [
        SpeakerMap(
            speaker=str(row["speaker"]),
            mapped_name=None if row["label"] == "UNKNOWN" else str(row["label"]),
            confidence=row["confidence"],
            competing_matches=list(row["alternatives"]),
        )
        for row in mappings
    ]
    words = []
    for word in transcript.words:
        row = by_speaker.get(word.speaker or "")
        if row is None:
            words.append(word)
            continue
        words.append(
            word.model_copy(
                update={
                    "speaker_match_label": row["label"],
                    "speaker_match_confidence": row["confidence"],
                    "speaker_match_alternatives": row["alternatives"],
                    "speaker_match_ambiguous": row["ambiguous"],
                    "speaker_profile_id": row["source_profile_id"],
                }
            )
        )
    turns = []
    for turn in transcript.speaker_turns:
        row = by_speaker.get(turn.speaker)
        if row is None:
            turns.append(turn)
            continue
        turns.append(
            turn.model_copy(
                update={
                    "mapped_name": None if row["label"] == "UNKNOWN" else row["label"],
                    "confidence": row["confidence"],
                    "speaker_match_label": row["label"],
                    "speaker_match_confidence": row["confidence"],
                    "speaker_match_alternatives": row["alternatives"],
                    "speaker_match_ambiguous": row["ambiguous"],
                    "speaker_profile_id": row["source_profile_id"],
                }
            )
        )
    return transcript.model_copy(
        update={"words": words, "speaker_turns": turns, "speaker_map": speaker_maps}
    )


def average_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("At least one enrollment embedding is required.")
    validated = [
        validate_embedding(vector, label=f"Enrollment embedding {index}")
        for index, vector in enumerate(vectors)
    ]
    width = len(validated[0])
    if any(len(vector) != width for vector in validated):
        raise ValueError("Enrollment samples produced incompatible embedding sizes.")
    averaged = [
        math.fsum(vector[index] for vector in validated) / len(validated) for index in range(width)
    ]
    return validate_embedding(averaged, label="Averaged enrollment embedding")


def profile_metadata_path(name: str) -> Path:
    root = speaker_home()
    path = _profile_dir(name) / "profile.json"
    return _assert_contained(path, root, label="Speaker profile metadata")


def write_profiles_index() -> Path:
    root = _ensure_private_dir(speaker_home())
    path = root / "speaker_profiles.json"
    _assert_contained(path, root, label="Speaker profile index")
    _private_write_json(
        path, {"profiles": [profile.model_dump(mode="json") for profile in list_enrollments()]}
    )
    return path
