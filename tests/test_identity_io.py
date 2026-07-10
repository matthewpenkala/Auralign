# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from local_transcript_max.export.markdown_export import render_markdown
from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript
from local_transcript_max.speaker_id import identity_io
from local_transcript_max.speaker_id.identity_io import (
    IdentityArtifactTransaction,
    IdentityResolverError,
    JobIdentityLock,
    canonical_hash_snapshot,
    classify_markdown_drift,
    deterministic_json_bytes,
    job_artifact_path,
    load_identity_job,
    read_json_model,
    read_optional_bytes,
    resolve_job_dir,
    serialize_jsonl,
    stable_digest,
    strict_json_bytes,
    verify_canonical_hashes,
)
from local_transcript_max.speaker_id.identity_models import (
    DriftClassification,
    MachineReadableError,
    ResolverPolicy,
)

HASH = "b" * 64
RUN_ID = "run:0123456789abcdef01234567"


def synthetic_transcript() -> Transcript:
    folded_word = AlignedWord(
        text="Synthetic",
        start=0.0,
        end=0.5,
        speaker="UNKNOWN",
        uncertain_speaker=True,
    )
    ordinary_word = AlignedWord(
        text="response",
        start=0.6,
        end=1.0,
        speaker="SPEAKER_01",
    )
    return Transcript(
        input_path="synthetic.wav",
        words=[folded_word, ordinary_word],
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=0.5,
                text="Synthetic",
                words=[folded_word],
            ),
            SpeakerTurn(
                speaker="SPEAKER_01",
                start=0.6,
                end=1.0,
                text="response",
                words=[ordinary_word],
            ),
        ],
    )


def write_synthetic_job(job_dir: Path) -> Transcript:
    job_dir.mkdir()
    transcript = synthetic_transcript()
    (job_dir / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (job_dir / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    (job_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "input_file_hash": HASH,
                "processing_timestamp": "2026-07-09T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return transcript


@pytest.mark.parametrize(
    "data",
    [
        b'{"key":1,"key":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b"\xff",
    ],
)
def test_strict_json_rejects_duplicate_keys_non_finite_numbers_and_invalid_utf8(
    data: bytes,
) -> None:
    with pytest.raises(ValueError):
        strict_json_bytes(data)


def test_strict_json_enforces_depth_and_item_count_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = ("[" * identity_io.MAX_JSON_DEPTH + "0" + "]" * identity_io.MAX_JSON_DEPTH).encode()
    with pytest.raises(ValueError, match="nesting exceeds"):
        strict_json_bytes(nested)

    monkeypatch.setattr(identity_io, "MAX_JSON_ITEMS", 4)
    with pytest.raises(ValueError, match="item count exceeds"):
        strict_json_bytes(b"[1,2,3,4]")


def test_json_reader_enforces_byte_limit_and_wraps_schema_failures(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text('{"schema_version":"speaker_identity_error.v1","code":"x","message":"y"}')
    with pytest.raises(IdentityResolverError) as oversized:
        read_json_model(path, MachineReadableError, max_bytes=4)
    assert oversized.value.code == "artifact_too_large"

    path.write_text("{}", encoding="utf-8")
    with pytest.raises(IdentityResolverError) as malformed:
        read_json_model(path, MachineReadableError)
    assert malformed.value.code == "malformed_artifact"
    assert str(path.parent) not in str(malformed.value)


def test_readers_require_regular_non_symlink_files(tmp_path: Path) -> None:
    regular = tmp_path / "regular.json"
    regular.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(regular)
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(IdentityResolverError) as symlink_error:
        read_optional_bytes(link)
    assert symlink_error.value.code == "unsafe_symlink"
    with pytest.raises(IdentityResolverError) as type_error:
        read_optional_bytes(directory)
    assert type_error.value.code == "unsafe_artifact_type"


def test_job_directory_and_artifact_path_guards(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    job_link = tmp_path / "job-link"
    job_link.symlink_to(job, target_is_directory=True)
    regular = tmp_path / "file"
    regular.write_text("not a directory", encoding="utf-8")

    assert resolve_job_dir(job) == job.resolve()
    with pytest.raises(IdentityResolverError) as symlink_error:
        resolve_job_dir(job_link)
    assert symlink_error.value.code == "unsafe_job_symlink"
    with pytest.raises(IdentityResolverError) as file_error:
        resolve_job_dir(regular)
    assert file_error.value.code == "invalid_job"
    with pytest.raises(IdentityResolverError) as missing_error:
        resolve_job_dir(tmp_path / "missing")
    assert missing_error.value.code == "missing_job"

    assert job_artifact_path(job, "transcript.json", allowed={"transcript.json"}) == (
        job / "transcript.json"
    )
    for name in ["../transcript.json", "sub/transcript.json", "sub\\transcript.json", "", "."]:
        with pytest.raises(IdentityResolverError):
            job_artifact_path(job, name)
    with pytest.raises(IdentityResolverError) as allowlist_error:
        job_artifact_path(job, "private.json", allowed={"transcript.json"})
    assert allowlist_error.value.code == "artifact_not_allowed"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (
            b"# Transcript\n\n**SPEAKER_00**  \nSynthetic line.\n",
            DriftClassification.NONE,
        ),
        (
            b"# Transcript\n\n**SPEAKER_01**  \nSynthetic line.\n",
            DriftClassification.LABEL_ONLY,
        ),
        (
            b"# Transcript\n\n**SPEAKER_00**  \nChanged line...\n",
            DriftClassification.TEXT,
        ),
        (
            b"# Transcript\n\nSPEAKER_00       \nSynthetic line.\n",
            DriftClassification.SEGMENTATION,
        ),
        (
            b"# Transcript\n\n**SPEAKER_00**  \nSynthetic line.\n\n",
            DriftClassification.LINE_COUNT,
        ),
    ],
)
def test_markdown_drift_classifies_every_supported_class(
    target: bytes, expected: DriftClassification
) -> None:
    canonical = b"# Transcript\n\n**SPEAKER_00**  \nSynthetic line.\n"
    result = classify_markdown_drift(canonical, target)
    assert result.drift_classification is expected
    assert result.artifact.relative_path == "external_transcript"
    assert result.artifact.sha256 == hashlib.sha256(target).hexdigest()


def test_job_context_is_private_relocation_stable_and_detects_folded_unknown(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    transcript = write_synthetic_job(first_dir)
    write_synthetic_job(second_dir)
    external_dir = tmp_path / "private-review-location"
    external_dir.mkdir()
    external = external_dir / "review.md"
    external.write_text(render_markdown(transcript), encoding="utf-8")

    first = load_identity_job(
        first_dir,
        policy=ResolverPolicy(),
        known_speakers=2,
        external_transcript=external,
    )
    second = load_identity_job(
        second_dir,
        policy=ResolverPolicy(),
        known_speakers=2,
        external_transcript=external,
    )

    assert first.context.run_id == second.context.run_id
    assert first.context.job_fingerprint == second.context.job_fingerprint
    assert first.context.job_ref == "."
    assert first.context.input_hash == HASH
    serialized = first.context.model_dump_json()
    assert str(first_dir) not in serialized
    assert str(second_dir) not in serialized
    assert str(external_dir) not in serialized
    assert all(
        reference.relative_path is None or not Path(reference.relative_path).is_absolute()
        for reference in first.context.artifacts
    )
    assert first.turn_provenance[0].folded_unknown is True
    assert first.turn_provenance[0].canonical_label == "UNKNOWN"
    assert first.turn_provenance[0].original_diarization_label == "SPEAKER_00"
    assert first.turn_provenance[0].source_label_provenance == (
        "canonical_unknown_folded_into_cluster"
    )
    assert first.turn_provenance[0].word_index_start == 0
    assert first.turn_provenance[1].folded_unknown is False


def test_job_context_marks_malformed_optional_json_consistently(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_synthetic_job(job_dir)
    (job_dir / "speaker_report.json").write_text("{", encoding="utf-8")
    (job_dir / "run_metadata.json").write_text("{", encoding="utf-8")

    loaded = load_identity_job(job_dir, policy=ResolverPolicy())
    assert {"speaker_report.json", "run_metadata.json"} <= set(loaded.context.malformed_artifacts)
    references = {item.relative_path: item for item in loaded.context.artifacts}
    assert references["speaker_report.json"].state.value == "malformed"
    assert references["run_metadata.json"].state.value == "malformed"


def test_invalid_run_metadata_hash_fails_with_privacy_safe_resolver_error(tmp_path: Path) -> None:
    job_dir = tmp_path / "private-job-name"
    write_synthetic_job(job_dir)
    (job_dir / "run_metadata.json").write_text(
        json.dumps({"input_file_hash": "z" * 64}), encoding="utf-8"
    )

    with pytest.raises(IdentityResolverError) as malformed:
        load_identity_job(job_dir, policy=ResolverPolicy())
    assert malformed.value.code == "invalid_input_hash"
    assert str(job_dir) not in str(malformed.value)


def test_canonical_and_external_transcript_symlinks_fail_closed(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_synthetic_job(job_dir)
    real_transcript = job_dir / "transcript.real.json"
    (job_dir / "transcript.json").replace(real_transcript)
    (job_dir / "transcript.json").symlink_to(real_transcript)
    with pytest.raises(IdentityResolverError) as canonical_error:
        load_identity_job(job_dir, policy=ResolverPolicy())
    assert canonical_error.value.code == "missing_canonical_transcript"

    (job_dir / "transcript.json").unlink()
    real_transcript.replace(job_dir / "transcript.json")
    external = tmp_path / "external.md"
    external.write_text("# Transcript\n", encoding="utf-8")
    external_link = tmp_path / "external-link.md"
    external_link.symlink_to(external)
    with pytest.raises(IdentityResolverError) as external_error:
        load_identity_job(
            job_dir,
            policy=ResolverPolicy(),
            external_transcript=external_link,
        )
    assert external_error.value.code == "unsafe_symlink"


def test_run_id_changes_only_when_resolution_inputs_change(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_synthetic_job(job_dir)
    baseline = load_identity_job(job_dir, policy=ResolverPolicy())
    identical = load_identity_job(job_dir, policy=ResolverPolicy())
    changed_mode = load_identity_job(
        job_dir,
        policy=ResolverPolicy(mode="apply-safe"),
    )
    changed_count = load_identity_job(job_dir, policy=ResolverPolicy(), known_speakers=2)

    assert baseline.context.run_id == identical.context.run_id
    assert baseline.context.job_fingerprint == changed_mode.context.job_fingerprint
    assert baseline.context.job_fingerprint == changed_count.context.job_fingerprint
    assert baseline.context.run_id != changed_mode.context.run_id
    assert baseline.context.run_id != changed_count.context.run_id


def test_job_hash_snapshot_detects_mutation_without_changing_canonical_files(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_synthetic_job(job_dir)
    job = load_identity_job(job_dir, policy=ResolverPolicy())
    snapshot = canonical_hash_snapshot(job)
    assert verify_canonical_hashes(job, snapshot)

    before = (job_dir / "transcript.json").read_bytes()
    (job_dir / "transcript.md").write_text("changed", encoding="utf-8")
    assert not verify_canonical_hashes(job, snapshot)
    assert (job_dir / "transcript.json").read_bytes() == before


def test_stable_digest_and_json_serialization_are_key_order_independent() -> None:
    first = {"b": [2, 1], "a": "value"}
    second = {"a": "value", "b": [2, 1]}
    assert deterministic_json_bytes(first) == deterministic_json_bytes(second)
    assert stable_digest(first, prefix="scope") == stable_digest(second, prefix="scope")
    assert deterministic_json_bytes({"label": "Participant α"}).decode().endswith("\n")


def test_jsonl_is_deterministic_and_never_appends_implicitly() -> None:
    first = MachineReadableError(code="a", message="first")
    second = MachineReadableError(code="b", message="second")
    payload = serialize_jsonl(iter([first, second]))
    assert payload.count(b"\n") == 2
    assert [strict_json_bytes(line) for line in payload.splitlines()] == [
        first.model_dump(mode="json"),
        second.model_dump(mode="json"),
    ]


def test_job_lock_blocks_concurrent_entry_and_uses_private_mode(tmp_path: Path) -> None:
    with JobIdentityLock(tmp_path) as lock:
        assert lock.path.exists()
        assert stat.S_IMODE(lock.path.stat().st_mode) == 0o600
        with pytest.raises(IdentityResolverError) as busy:
            with JobIdentityLock(tmp_path):
                pass
        assert busy.value.code == "resolver_busy"
    assert lock.path.exists()


def test_job_lock_uses_descriptor_ownership_not_pid_file_age(tmp_path: Path) -> None:
    lock_path = tmp_path / ".speaker_identity.lock"
    lock_path.write_bytes(deterministic_json_bytes({"pid": 424242, "created": 1}))
    with JobIdentityLock(tmp_path):
        assert lock_path.exists()
    with JobIdentityLock(tmp_path):
        assert lock_path.exists()


def stage_complete_family(transaction: IdentityArtifactTransaction, marker: bytes) -> None:
    transaction.stage_bytes("speaker_identity_candidates.json", b"candidate-" + marker)
    transaction.stage_bytes("speaker_identity_evidence.jsonl", b"evidence-" + marker)
    transaction.stage_bytes("speaker_identity_report.json", b"report-" + marker)


def test_transaction_publishes_report_last_with_private_modes_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    real_replace = identity_io.os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(identity_io.os, "replace", recording_replace)
    for _ in range(2):
        with IdentityArtifactTransaction(tmp_path, RUN_ID) as transaction:
            assert stat.S_IMODE(transaction.stage.stat().st_mode) == 0o700
            stage_complete_family(transaction, b"same")
            assert all(
                stat.S_IMODE((transaction.stage / name).stat().st_mode) == 0o600
                for name in [
                    "speaker_identity_candidates.json",
                    "speaker_identity_evidence.jsonl",
                    "speaker_identity_report.json",
                ]
            )
            transaction.publish()
        assert not transaction.stage.exists()

    destinations = [destination.name for _, destination in calls]
    report_indexes = [
        index for index, name in enumerate(destinations) if name == "speaker_identity_report.json"
    ]
    assert report_indexes
    assert report_indexes[-1] == len(destinations) - 1
    assert (tmp_path / "speaker_identity_report.json").read_bytes() == b"report-same"
    assert stat.S_IMODE((tmp_path / "speaker_identity_report.json").stat().st_mode) == 0o600


def test_transaction_rolls_back_all_artifacts_after_injected_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = [
        "speaker_identity_candidates.json",
        "speaker_identity_evidence.jsonl",
        "speaker_identity_report.json",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"old-" + name.encode())

    real_replace = identity_io.os.replace
    failed = False

    def fail_mid_publication(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed
            and source_path.name == "speaker_identity_evidence.jsonl"
            and source_path.parent.name.startswith(".speaker_identity.")
            and destination_path.parent == tmp_path
        ):
            failed = True
            raise OSError("synthetic replace interruption")
        real_replace(source, destination)

    monkeypatch.setattr(identity_io.os, "replace", fail_mid_publication)
    with pytest.raises(OSError, match="synthetic replace interruption"):
        with IdentityArtifactTransaction(tmp_path, RUN_ID) as transaction:
            stage_complete_family(transaction, b"new")
            transaction.publish()

    assert failed
    for name in names:
        assert (tmp_path / name).read_bytes() == b"old-" + name.encode()
    assert not list(tmp_path.glob(".speaker_identity.*"))


def test_transaction_rejects_symlink_and_unallowlisted_outputs(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"unchanged")
    unsafe = tmp_path / "speaker_identity_candidates.json"
    unsafe.symlink_to(outside)

    with IdentityArtifactTransaction(tmp_path, RUN_ID) as transaction:
        with pytest.raises(IdentityResolverError) as symlink_error:
            transaction.stage_bytes("speaker_identity_candidates.json", b"private")
        assert symlink_error.value.code == "unsafe_output_symlink"
        with pytest.raises(IdentityResolverError) as allowlist_error:
            transaction.stage_bytes("transcript.json", b"private")
        assert allowlist_error.value.code == "artifact_not_allowed"
    assert outside.read_bytes() == b"unchanged"


def test_transaction_requires_report_commit_marker_and_cleans_partial_stage(
    tmp_path: Path,
) -> None:
    with pytest.raises(IdentityResolverError) as missing:
        with IdentityArtifactTransaction(tmp_path, RUN_ID) as transaction:
            transaction.stage_bytes("speaker_identity_candidates.json", b"candidate")
            transaction.publish()
    assert missing.value.code == "missing_commit_marker"
    assert not (tmp_path / "speaker_identity_candidates.json").exists()
    assert not list(tmp_path.glob(".speaker_identity.*"))
