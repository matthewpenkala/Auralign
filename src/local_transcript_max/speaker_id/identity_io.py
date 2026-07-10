# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Bounded artifact loading and transactional publication for identity resolution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from local_transcript_max.errors import LTMError
from local_transcript_max.schemas import Transcript

from .identity_models import (
    ArtifactReference,
    ArtifactState,
    DriftClassification,
    ExternalTranscriptContext,
    ResolverContext,
    ResolverPolicy,
)

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_ITEMS = 500_000
LOCK_STALE_SECONDS = 60 * 60

CANONICAL_ARTIFACTS: tuple[str, ...] = (
    "transcript.json",
    "transcript.md",
    "transcript.srt",
    "transcript.vtt",
    "transcript.txt",
    "transcript.segments.csv",
    "transcript.rttm",
    "transcript.raw.json",
    "transcript.aligned.json",
    "transcript.diarized.json",
    "diarization.json",
    "exclusive_diarization.json",
    "vad.json",
    "speaker_report.json",
    "speaker_validation_report.json",
    "qa_report.json",
    "run_metadata.json",
    "speaker_map.json",
    "speaker_cluster_embeddings.json",
)

MANAGED_IDENTITY_ARTIFACTS: frozenset[str] = frozenset(
    {
        "speaker_identity_report.json",
        "speaker_identity_candidates.json",
        "speaker_identity_evidence.jsonl",
        "speaker_identity_attribution_audit.json",
        "speaker_identity_attribution_audit.csv",
        "speaker_identity_diarization_stability.json",
        "speaker_identity_media_review_queue.json",
        "transcript.identity_mapped.json",
        "transcript.identity_mapped.srt",
        "transcript.identity_mapped.md",
        "transcript.identity_mapped.vtt",
        "transcript.identity_mapped.txt",
        "transcript.identity_mapped.segments.csv",
    }
)

HUMAN_OWNED_ARTIFACTS: frozenset[str] = frozenset(
    {"speaker_identity_approved_map.json", "speaker_identity_media_review_report.json"}
)


class IdentityResolverError(LTMError):
    """A privacy-safe, actionable identity resolver error."""

    def __init__(self, code: str, message: str, remediation: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation


class DuplicateJSONKeyError(ValueError):
    """Raised for ambiguous JSON objects."""


TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(frozen=True)
class ReadArtifact:
    path: Path
    data: bytes
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class TurnProvenance:
    canonical_label: str
    original_diarization_label: str | None
    source_label_provenance: str
    word_index_start: int | None
    word_index_end: int | None
    folded_unknown: bool
    uncertain: bool


@dataclass(frozen=True)
class LoadedIdentityJob:
    job_dir: Path
    transcript: Transcript
    transcript_artifact: ReadArtifact
    context: ResolverContext
    artifact_bytes: Mapping[str, ReadArtifact]
    turn_provenance: tuple[TurnProvenance, ...]


def _object_pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_bytes(data: bytes) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON must be valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    value = json.loads(
        text,
        object_pairs_hook=_object_pairs_no_duplicates,
        parse_constant=reject_constant,
    )
    _validate_json_shape(value)
    return value


def _validate_json_shape(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    item_count = 0
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds the resolver limit")
        item_count += 1
        if item_count > MAX_JSON_ITEMS:
            raise ValueError("JSON item count exceeds the resolver limit")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _read_regular(path: Path, *, max_bytes: int) -> ReadArtifact:
    if path.is_symlink():
        raise IdentityResolverError("unsafe_symlink", "Resolver inputs cannot be symlinks.")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise IdentityResolverError(
            "missing_artifact", "A required resolver artifact is missing."
        ) from exc
    except OSError as exc:
        raise IdentityResolverError(
            "unsafe_artifact", "A resolver artifact could not be opened safely."
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise IdentityResolverError(
                "unsafe_artifact_type", "Resolver inputs must be regular files."
            )
        if metadata.st_size > max_bytes:
            raise IdentityResolverError(
                "artifact_too_large", "A resolver input exceeds its size limit."
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise IdentityResolverError(
                "artifact_too_large", "A resolver input exceeds its size limit."
            )
        return ReadArtifact(
            path=path,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
    finally:
        os.close(descriptor)


def read_json_model(path: Path, model: type[TModel], *, max_bytes: int = MAX_JSON_BYTES) -> TModel:
    artifact = _read_regular(path, max_bytes=max_bytes)
    try:
        return model.model_validate(strict_json_bytes(artifact.data))
    except Exception as exc:
        raise IdentityResolverError(
            "malformed_artifact",
            f"The resolver input {path.name!r} is malformed.",
        ) from exc


def resolve_job_dir(job_dir: Path) -> Path:
    expanded = job_dir.expanduser()
    if expanded.is_symlink():
        raise IdentityResolverError("unsafe_job_symlink", "JOB_DIR cannot be a symlink.")
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise IdentityResolverError("missing_job", "JOB_DIR does not exist.") from exc
    if not resolved.is_dir():
        raise IdentityResolverError("invalid_job", "JOB_DIR must be a directory.")
    return resolved


def job_artifact_path(job_dir: Path, name: str, *, allowed: set[str] | None = None) -> Path:
    if allowed is not None and name not in allowed:
        raise IdentityResolverError(
            "artifact_not_allowed", "The requested artifact is not allowlisted."
        )
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise IdentityResolverError(
            "invalid_artifact_id", "Artifact identifiers must be basenames."
        )
    return job_dir / name


def artifact_reference(
    name: str, artifact: ReadArtifact | None, *, malformed: bool = False
) -> ArtifactReference:
    state = (
        ArtifactState.MALFORMED
        if malformed
        else (ArtifactState.PRESENT if artifact is not None else ArtifactState.MISSING)
    )
    return ArtifactReference(
        artifact_id=name.replace(".", "_"),
        relative_path=name,
        sha256=None if artifact is None else artifact.sha256,
        size_bytes=None if artifact is None else artifact.size_bytes,
        state=state,
        error_code="malformed_artifact" if malformed else None,
    )


def parse_markdown_turns(data: bytes) -> tuple[list[str], list[str], list[str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityResolverError(
            "invalid_markdown", "Transcript Markdown must be UTF-8."
        ) from exc
    lines = text.splitlines()
    labels: list[str] = []
    non_label_lines: list[str] = []
    label_line_indexes: list[str] = []
    label_pattern = re_compile_label()
    for index, line in enumerate(lines, start=1):
        match = label_pattern.fullmatch(line)
        if match:
            labels.append(match.group(1).strip())
            label_line_indexes.append(str(index))
        elif not (index == 1 and line.strip() == "# Transcript"):
            non_label_lines.append(line)
    return labels, non_label_lines, label_line_indexes


def re_compile_label() -> Any:
    # Kept lazy so importing the resolver does not compile transcript patterns until needed.
    import re

    return re.compile(r"\*\*(.+?)\*\*\s{0,2}")


def classify_markdown_drift(canonical: bytes, target: bytes) -> ExternalTranscriptContext:
    target_artifact = ReadArtifact(
        path=Path("external-transcript"),
        data=target,
        sha256=hashlib.sha256(target).hexdigest(),
        size_bytes=len(target),
    )
    canonical_lines = canonical.decode("utf-8").splitlines()
    target_lines = target.decode("utf-8").splitlines()
    canonical_labels, canonical_text, _ = parse_markdown_turns(canonical)
    target_labels, target_text, _ = parse_markdown_turns(target)
    if canonical == target:
        drift = DriftClassification.NONE
    elif len(canonical_lines) != len(target_lines):
        drift = DriftClassification.LINE_COUNT
    elif len(canonical_labels) != len(target_labels):
        drift = DriftClassification.SEGMENTATION
    elif canonical_text != target_text:
        drift = DriftClassification.TEXT
    elif canonical_labels != target_labels:
        drift = DriftClassification.LABEL_ONLY
    else:
        drift = DriftClassification.NONE
    return ExternalTranscriptContext(
        artifact=artifact_reference("external_transcript", target_artifact),
        drift_classification=drift,
        line_count_matches=len(canonical_lines) == len(target_lines),
        turn_count_matches=len(canonical_labels) == len(target_labels),
        non_label_text_matches=canonical_text == target_text,
        target_labels=target_labels,
    )


def deterministic_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def stable_digest(value: Any, *, prefix: str, length: int = 24) -> str:
    digest = hashlib.sha256(deterministic_json_bytes(value)).hexdigest()
    return f"{prefix}:{digest[:length]}"


def _stable_generated_at(run_metadata: Mapping[str, Any] | None) -> str:
    if run_metadata is not None:
        value = run_metadata.get("processing_timestamp")
        if isinstance(value, str) and value:
            return value
    return "1970-01-01T00:00:00Z"


def load_identity_job(
    job_dir: Path,
    *,
    policy: ResolverPolicy,
    known_speakers: int | None = None,
    external_transcript: Path | None = None,
    optional_input_hashes: Mapping[str, str] | None = None,
) -> LoadedIdentityJob:
    root = resolve_job_dir(job_dir)
    artifacts: dict[str, ReadArtifact] = {}
    references: list[ArtifactReference] = []
    missing: list[str] = []
    malformed: list[str] = []
    for name in CANONICAL_ARTIFACTS:
        path = job_artifact_path(root, name, allowed=set(CANONICAL_ARTIFACTS))
        if not path.exists():
            references.append(artifact_reference(name, None))
            missing.append(name)
            continue
        try:
            artifact = _read_regular(
                path,
                max_bytes=MAX_TEXT_BYTES if name.endswith(".md") else MAX_JSON_BYTES,
            )
        except IdentityResolverError:
            references.append(artifact_reference(name, None, malformed=True))
            malformed.append(name)
            continue
        if name.endswith(".json"):
            try:
                strict_json_bytes(artifact.data)
            except Exception:
                references.append(artifact_reference(name, None, malformed=True))
                malformed.append(name)
                continue
        artifacts[name] = artifact
        references.append(artifact_reference(name, artifact))
    transcript_artifact = artifacts.get("transcript.json")
    if transcript_artifact is None:
        raise IdentityResolverError(
            "missing_canonical_transcript",
            "transcript.json is required for identity resolution.",
        )
    try:
        transcript = Transcript.model_validate(strict_json_bytes(transcript_artifact.data))
    except Exception as exc:
        raise IdentityResolverError(
            "malformed_canonical_transcript",
            "transcript.json does not match the supported canonical schema.",
        ) from exc

    external_context: ExternalTranscriptContext | None = None
    if external_transcript is not None:
        canonical_markdown = artifacts.get("transcript.md")
        if canonical_markdown is None:
            raise IdentityResolverError(
                "missing_canonical_markdown",
                "transcript.md is required when --external-transcript is used.",
            )
        external_artifact = _read_regular(
            external_transcript.expanduser(), max_bytes=MAX_TEXT_BYTES
        )
        external_context = classify_markdown_drift(canonical_markdown.data, external_artifact.data)

    run_metadata_payload: Mapping[str, Any] | None = None
    run_metadata = artifacts.get("run_metadata.json")
    if run_metadata is not None:
        try:
            payload = strict_json_bytes(run_metadata.data)
            if isinstance(payload, dict):
                run_metadata_payload = cast(Mapping[str, Any], payload)
        except Exception:
            malformed.append("run_metadata.json")
    input_hash = None
    if run_metadata_payload is not None:
        candidate_hash = run_metadata_payload.get("input_file_hash")
        if isinstance(candidate_hash, str):
            if not re_full_sha256(candidate_hash):
                raise IdentityResolverError(
                    "invalid_input_hash",
                    "run_metadata.json contains an invalid input hash.",
                )
            input_hash = candidate_hash.lower()

    fingerprint_payload = {
        "transcript_json": transcript_artifact.sha256,
        "input_hash": input_hash,
    }
    job_fingerprint = stable_digest(fingerprint_payload, prefix="job")
    run_payload = {
        "job_fingerprint": job_fingerprint,
        "artifact_hashes": {
            name: artifact.sha256 for name, artifact in sorted(artifacts.items())
        },
        "policy": policy.model_dump(mode="json"),
        "known_speakers": known_speakers,
        "external_hash": None if external_context is None else external_context.artifact.sha256,
        "optional_input_hashes": dict(sorted((optional_input_hashes or {}).items())),
    }
    run_id = stable_digest(run_payload, prefix="run")
    context = ResolverContext(
        run_id=run_id,
        job_fingerprint=job_fingerprint,
        input_hash=input_hash,
        artifacts=references,
        external_transcript=external_context,
        known_speakers=known_speakers,
        artifacts_read=sorted(artifacts),
        missing_artifacts=sorted(set(missing)),
        malformed_artifacts=sorted(set(malformed)),
        generated_at=_stable_generated_at(run_metadata_payload),
    )
    return LoadedIdentityJob(
        job_dir=root,
        transcript=transcript,
        transcript_artifact=transcript_artifact,
        context=context,
        artifact_bytes=artifacts,
        turn_provenance=tuple(build_turn_provenance(transcript)),
    )


def build_turn_provenance(transcript: Transcript) -> list[TurnProvenance]:
    word_identity = {id(word): index for index, word in enumerate(transcript.words)}
    provenance: list[TurnProvenance] = []
    for turn in transcript.speaker_turns:
        indexes = [word_identity[id(word)] for word in turn.words if id(word) in word_identity]
        # Parsed Pydantic child objects are usually distinct; fall back to stable coordinates.
        if not indexes and turn.words:
            for turn_word in turn.words:
                for index, word in enumerate(transcript.words):
                    if (
                        word.start == turn_word.start
                        and word.end == turn_word.end
                        and word.text == turn_word.text
                    ):
                        indexes.append(index)
                        break
        word_labels = [word.speaker for word in turn.words]
        folded_unknown = bool(word_labels) and all(
            label is None or label.upper() == "UNKNOWN" for label in word_labels
        )
        uncertain = folded_unknown or any(word.uncertain_speaker for word in turn.words)
        canonical_label = "UNKNOWN" if folded_unknown else turn.speaker
        provenance.append(
            TurnProvenance(
                canonical_label=canonical_label,
                original_diarization_label=turn.speaker,
                source_label_provenance=(
                    "canonical_unknown_folded_into_cluster" if folded_unknown else "canonical"
                ),
                word_index_start=min(indexes) if indexes else None,
                word_index_end=max(indexes) if indexes else None,
                folded_unknown=folded_unknown,
                uncertain=uncertain,
            )
        )
    return provenance


def re_full_sha256(value: str) -> bool:
    import re

    return re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def canonical_hash_snapshot(job: LoadedIdentityJob) -> dict[str, str]:
    return {
        name: artifact.sha256
        for name, artifact in job.artifact_bytes.items()
        if name not in {"speaker_map.json", "speaker_cluster_embeddings.json"}
    }


def verify_canonical_hashes(job: LoadedIdentityJob, expected: Mapping[str, str]) -> bool:
    for name, digest in expected.items():
        path = job_artifact_path(job.job_dir, name, allowed=set(CANONICAL_ARTIFACTS))
        try:
            current = _read_regular(
                path,
                max_bytes=MAX_TEXT_BYTES if name.endswith(".md") else MAX_JSON_BYTES,
            )
        except IdentityResolverError:
            return False
        if current.sha256 != digest:
            return False
    return True


class JobIdentityLock(AbstractContextManager["JobIdentityLock"]):
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir
        self.path = job_dir / ".speaker_identity.lock"
        self.acquired = False

    def __enter__(self) -> JobIdentityLock:
        self._clear_stale_lock()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise IdentityResolverError(
                "resolver_busy",
                "Another resolver or protected job operation is active.",
                "Wait for that operation to finish, then retry.",
            ) from exc
        try:
            payload = deterministic_json_bytes({"pid": os.getpid(), "created": int(time.time())})
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.acquired = True
        return self

    def _clear_stale_lock(self) -> None:
        if not self.path.exists() or self.path.is_symlink():
            return
        try:
            artifact = _read_regular(self.path, max_bytes=4096)
            payload = strict_json_bytes(artifact.data)
            pid = int(payload.get("pid", -1)) if isinstance(payload, dict) else -1
            created = int(payload.get("created", 0)) if isinstance(payload, dict) else 0
        except Exception:
            return
        if time.time() - created < LOCK_STALE_SECONDS:
            return
        try:
            os.kill(pid, 0)
        except PermissionError:
            return
        except (ProcessLookupError, ValueError):
            self.path.unlink(missing_ok=True)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
        self.acquired = False


class IdentityArtifactTransaction(AbstractContextManager["IdentityArtifactTransaction"]):
    """Publish a complete derived family or preserve the last complete family."""

    def __init__(self, job_dir: Path, run_id: str) -> None:
        self.job_dir = job_dir
        self.run_id = run_id
        self.stage = Path(
            mkdtemp(prefix=f".speaker_identity.{run_id.replace(':', '-')}.", dir=job_dir)
        )
        os.chmod(self.stage, 0o700)
        self.backup = self.stage / "backup"
        self.backup.mkdir(mode=0o700)
        self.closed = False

    def __enter__(self) -> IdentityArtifactTransaction:
        return self

    def stage_bytes(self, name: str, data: bytes) -> None:
        if name not in MANAGED_IDENTITY_ARTIFACTS:
            raise IdentityResolverError(
                "artifact_not_allowed", "Resolver output is not allowlisted."
            )
        destination = self.job_dir / name
        if destination.is_symlink():
            raise IdentityResolverError(
                "unsafe_output_symlink", "Resolver outputs cannot be symlinks."
            )
        path = self.stage / name
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def publish(self, *, report_name: str = "speaker_identity_report.json") -> None:
        staged = {path.name for path in self.stage.iterdir() if path.is_file()}
        if report_name not in staged:
            raise IdentityResolverError("missing_commit_marker", "Resolver report was not staged.")
        published: list[str] = []
        backed_up: list[str] = []
        try:
            for name in sorted(MANAGED_IDENTITY_ARTIFACTS - {report_name}):
                destination = self.job_dir / name
                if destination.exists():
                    if destination.is_symlink() or not destination.is_file():
                        raise IdentityResolverError(
                            "unsafe_existing_output", "An existing resolver output is unsafe."
                        )
                    os.replace(destination, self.backup / name)
                    backed_up.append(name)
                source = self.stage / name
                if source.exists():
                    os.replace(source, destination)
                    published.append(name)
            report_destination = self.job_dir / report_name
            if report_destination.exists():
                if report_destination.is_symlink() or not report_destination.is_file():
                    raise IdentityResolverError(
                        "unsafe_existing_output", "The existing resolver report is unsafe."
                    )
                os.replace(report_destination, self.backup / report_name)
                backed_up.append(report_name)
            os.replace(self.stage / report_name, report_destination)
            published.append(report_name)
            directory_descriptor = os.open(self.job_dir, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            for name in reversed(published):
                (self.job_dir / name).unlink(missing_ok=True)
            for name in reversed(backed_up):
                backup = self.backup / name
                if backup.exists():
                    os.replace(backup, self.job_dir / name)
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if not self.closed:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.closed = True


def serialize_jsonl(rows: Iterator[BaseModel]) -> bytes:
    chunks = [deterministic_json_bytes(row).rstrip(b"\n") for row in rows]
    return b"\n".join(chunks) + (b"\n" if chunks else b"")


def load_optional_model(
    path: Path | None,
    model: type[TModel],
    *,
    max_bytes: int = MAX_JSON_BYTES,
) -> tuple[TModel | None, str | None]:
    if path is None:
        return None, None
    artifact = _read_regular(path.expanduser(), max_bytes=max_bytes)
    try:
        value = model.model_validate(strict_json_bytes(artifact.data))
    except Exception as exc:
        raise IdentityResolverError(
            "malformed_optional_input", f"The resolver input {path.name!r} is malformed."
        ) from exc
    return value, artifact.sha256


def read_optional_bytes(
    path: Path | None, *, max_bytes: int = MAX_JSON_BYTES
) -> ReadArtifact | None:
    return None if path is None else _read_regular(path.expanduser(), max_bytes=max_bytes)


def guarded_call(code: str, callback: Callable[[], TModel]) -> TModel:
    try:
        return callback()
    except IdentityResolverError:
        raise
    except Exception as exc:
        raise IdentityResolverError(
            code, "The resolver could not process a validated input."
        ) from exc
