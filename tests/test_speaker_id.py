# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import json
import math
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.platform_security import private_path_is_secure
from local_transcript_max.speaker_id.embeddings import (
    extract_embedding,
    load_embedding,
    save_embedding,
)
from local_transcript_max.speaker_id.enrollment import (
    delete_speaker,
    enroll_speaker,
    export_profiles,
    import_profiles,
    inspect_speaker,
    load_validated_rename_evidence,
    match_job_speakers,
    rename_speaker,
)
from local_transcript_max.speaker_id.matching import cosine_similarity
from local_transcript_max.utils import write_json


def write_sample(path: Path) -> None:
    path.write_bytes(b"sample")


def test_enroll_inspect_export_import_delete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: ("mock", [1.0, 0.0]),
    )
    sample = tmp_path / "alice.wav"
    write_sample(sample)
    enrollment = enroll_speaker("Alice", sample)
    assert enrollment.name == "Alice"
    assert inspect_speaker("Alice").backend == "mock"
    export_path = export_profiles(tmp_path / "export.json")
    assert export_path.exists()
    assert import_profiles(export_path) == 1
    deleted = delete_speaker("Alice", yes=True)
    assert not deleted.exists()


def test_delete_requires_yes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    profile = tmp_path / "profiles" / "alice"
    profile.mkdir(parents=True)
    try:
        delete_speaker("Alice")
    except ValueError as exc:
        assert "--yes" in str(exc)


def test_confident_ambiguous_and_unknown_match(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: (
            "speechbrain-ecapa",
            [1.0, 0.0] if "alice" in path.name else [0.0, 1.0],
        ),
    )
    alice = tmp_path / "alice.wav"
    bob = tmp_path / "bob.wav"
    write_sample(alice)
    write_sample(bob)
    enroll_speaker("Alice", alice)
    enroll_speaker("Bob", bob)
    job = tmp_path / "job"
    job.mkdir()
    write_json(
        job / "transcript.json",
        {
            "input_path": "x.wav",
            "words": [
                {"text": "hello", "start": 0, "end": 1, "speaker": "SPEAKER_00"},
                {"text": "hmm", "start": 1, "end": 2, "speaker": "SPEAKER_01"},
            ],
            "speaker_turns": [
                {"speaker": "SPEAKER_00", "start": 0, "end": 1, "text": "hello"},
                {"speaker": "SPEAKER_01", "start": 1, "end": 2, "text": "hmm"},
            ],
            "diarization_turns": [
                {"start": 0, "end": 1, "speaker": "SPEAKER_00"},
                {"start": 1, "end": 2, "speaker": "SPEAKER_01"},
                {"start": 2, "end": 3, "speaker": "SPEAKER_02"},
            ],
        },
    )
    write_json(
        job / "speaker_cluster_embeddings.json",
        {
            "SPEAKER_00": [1.0, 0.0],
            "SPEAKER_01": [0.7, 0.7],
        },
    )
    result = match_job_speakers(job, threshold=0.75, ambiguity_margin=0.05)
    mappings = {row["speaker"]: row for row in result["mappings"]}
    assert mappings["SPEAKER_00"]["label"] == "Alice"
    assert mappings["SPEAKER_01"]["label"] == "UNKNOWN"
    assert mappings["SPEAKER_02"]["reason"] == "missing_cluster_embedding"
    assert (job / "speaker_map.json").exists()
    assert (job / "transcript.speaker_matched.json").exists()


def test_speaker_cli_delete_requires_yes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    result = CliRunner().invoke(app, ["speakers", "delete", "Alice"])
    assert result.exit_code == 1
    assert "--yes" in result.stderr


def test_speaker_map_redacts_profile_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: ("mock", [1.0, 0.0]),
    )
    sample = tmp_path / "alice.wav"
    write_sample(sample)
    enroll_speaker("Alice", sample)
    export_profiles(tmp_path / "export.json")
    text = (tmp_path / "export.json").read_text(encoding="utf-8")
    assert "hf_" not in text
    assert "sk-" not in text


def test_speaker_embedding_requires_local_ecapa_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LTM_MODEL_CACHE", str(tmp_path / "models"))
    sample = tmp_path / "alice.wav"
    write_sample(sample)

    with pytest.raises(RuntimeError, match="ltm models pull --speaker-id ecapa"):
        extract_embedding(sample)


@pytest.mark.parametrize(
    ("left", "right", "message"),
    [
        ([], [], "must not be empty"),
        ([1.0], [1.0, 0.0], "dimensions must match"),
        ([math.nan], [1.0], "must be finite"),
        ([math.inf], [1.0], "must be finite"),
        ([0.0, 0.0], [1.0, 0.0], "zero vector"),
        ([1.0] * 4097, [1.0] * 4097, "dimension limit"),
    ],
)
def test_cosine_similarity_rejects_invalid_vectors(
    left: list[float],
    right: list[float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        cosine_similarity(left, right)


def test_cosine_similarity_is_finite_and_bounded_for_large_values() -> None:
    score = cosine_similarity([1e308, 1e308], [1e308, 1e308])
    assert math.isfinite(score)
    assert -1.0 <= score <= 1.0
    assert score == pytest.approx(1.0)


def test_embedding_storage_rejects_nonfinite_and_symlink_files(tmp_path: Path) -> None:
    embedding = tmp_path / "embedding.json"
    with pytest.raises(ValueError, match="finite"):
        save_embedding(embedding, "speechbrain-ecapa", [math.nan])

    target = tmp_path / "target.json"
    target.write_text(
        json.dumps({"backend": "speechbrain-ecapa", "embedding": [1.0]}),
        encoding="utf-8",
    )
    embedding.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        load_embedding(embedding)


@pytest.mark.parametrize("value", [math.nan, math.inf, -0.01, 1.01])
def test_speaker_match_thresholds_must_be_finite_unit_intervals(
    tmp_path: Path,
    monkeypatch,
    value: float,
) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(ValueError, match=r"finite and in \[0, 1\]"):
        match_job_speakers(job, threshold=value)
    with pytest.raises(ValueError, match=r"finite and in \[0, 1\]"):
        match_job_speakers(job, ambiguity_margin=value)


def test_non_ecapa_profiles_are_typed_non_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: ("waveform-stats", [1.0, 0.0]),
    )
    sample = tmp_path / "sample.wav"
    write_sample(sample)
    enroll_speaker("Fallback Profile", sample)
    job = tmp_path / "job"
    job.mkdir()
    write_json(
        job / "transcript.json",
        {
            "input_path": "synthetic.wav",
            "diarization_turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}],
        },
    )
    write_json(job / "speaker_cluster_embeddings.json", {"SPEAKER_00": [1.0, 0.0]})

    result = match_job_speakers(job)

    mapping = result["mappings"][0]
    assert mapping["label"] == "UNKNOWN"
    assert mapping["reason"] == "local_profile_backend_unavailable"
    assert mapping["non_evidence_reasons"] == ["legacy_profile_backend_waveform_stats"]


def test_profile_export_is_portable_and_import_does_not_leak_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source-profiles"
    destination_root = tmp_path / "destination-profiles"
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(source_root))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: ("speechbrain-ecapa", [1.0, 0.0]),
    )
    sample = tmp_path / "portable.wav"
    write_sample(sample)
    enroll_speaker("Portable Profile", sample)
    export_path = export_profiles(tmp_path / "portable-profiles.json")

    exported_text = export_path.read_text(encoding="utf-8")
    exported = json.loads(exported_text)
    assert exported["schema_version"] == "speaker_profiles.v1"
    assert private_path_is_secure(export_path)
    assert "embedding_path" not in exported_text
    assert "sample_paths" not in exported_text
    assert str(source_root) not in exported_text

    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(destination_root))
    assert import_profiles(export_path) == 1
    imported = inspect_speaker("Portable Profile")
    assert Path(imported.embedding_path).is_relative_to(destination_root)
    assert load_embedding(Path(imported.embedding_path)) == (
        "speechbrain-ecapa",
        [1.0, 0.0],
    )


def test_legacy_import_never_dereferences_arbitrary_embedding_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "profiles"
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(root))
    outside = tmp_path / "outside-embedding.json"
    save_embedding(outside, "speechbrain-ecapa", [1.0, 0.0])
    payload = {
        "profiles": [
            {
                "name": "Unsafe Import",
                "embedding_path": str(outside),
                "sample_paths": [],
                "backend": "speechbrain-ecapa",
            }
        ]
    }
    import_path = tmp_path / "legacy.json"
    write_json(import_path, payload)

    with pytest.raises(ValueError, match="no safe local embedding"):
        import_profiles(import_path)
    assert outside.exists()
    assert not (root / "unsafe-import" / "profile.json").exists()


def test_profile_storage_permissions_and_slug_collisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "profiles"
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(root))
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.enrollment.extract_embedding",
        lambda path, allow_fallback=False: ("speechbrain-ecapa", [1.0, 0.0]),
    )
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    write_sample(first)
    write_sample(second)
    enroll_speaker("A B", first)

    profile_dir = root / "a-b"
    assert private_path_is_secure(root, directory=True)
    assert private_path_is_secure(profile_dir, directory=True)
    assert private_path_is_secure(profile_dir / "profile.json")
    assert private_path_is_secure(profile_dir / "embedding.json")
    assert private_path_is_secure(profile_dir / "samples" / "first.wav")

    with pytest.raises(ValueError, match="collide after slug normalization"):
        enroll_speaker("A-B", second)


def test_hash_bound_rename_evidence_rejects_stale_and_slug_collisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rename_root = tmp_path / "renames"
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(rename_root))
    job = tmp_path / "job"
    job.mkdir()
    write_json(job / "transcript.json", {"input_path": "synthetic.wav", "words": []})

    record = rename_speaker(str(job), "SPEAKER_00", "Participant A")
    evidence = load_validated_rename_evidence(job)
    assert [(row.speaker, row.name) for row in evidence] == [("SPEAKER_00", "Participant A")]
    assert len(evidence[0].record_hash) == 64
    assert evidence[0].record_id == f"rename:{evidence[0].record_hash[:16]}"
    assert private_path_is_secure(rename_root, directory=True)
    assert private_path_is_secure(record)

    write_json(
        job / "transcript.json",
        {"input_path": "synthetic.wav", "words": [{"text": "changed"}]},
    )
    assert load_validated_rename_evidence(job) == []

    rename_speaker("collision key", "SPEAKER_00", "One")
    with pytest.raises(ValueError, match="collide after slug normalization"):
        rename_speaker("collision-key", "SPEAKER_00", "Two")


def test_unbound_legacy_rename_records_are_not_safe_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rename_root = tmp_path / "renames"
    rename_root.mkdir()
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(rename_root))
    write_json(rename_root / "legacy.json", {"SPEAKER_00": "Participant A"})
    job = tmp_path / "job"
    job.mkdir()
    write_json(job / "transcript.json", {"input_path": "synthetic.wav"})

    assert load_validated_rename_evidence(job) == []
