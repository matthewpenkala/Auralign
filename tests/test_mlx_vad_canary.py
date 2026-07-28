# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import pytest
from pydantic import ValidationError

from local_transcript_max.benchmark.compare import write_comparison_csv
from local_transcript_max.benchmark.mlx_vad_canary import (
    MlxVadCanaryEvidence,
    MlxVadCanaryOutcome,
    MlxVadDeviceEvidence,
    MlxVadFailureCode,
    MlxVadModelBinding,
    MlxVadPerformanceEvidence,
    MlxVadRunEvidence,
    MlxVadRunStatus,
    MlxVadTextEvidence,
    MlxVadTimestampEvidence,
    MlxVadVariant,
    MlxVadWarmupEvidence,
    evaluate_mlx_vad_gate,
)


def _hash(character: str) -> str:
    return character * 64


def _timestamps(**updates: int) -> MlxVadTimestampEvidence:
    values = {
        "word_count": 100,
        "segment_count": 10,
        "missing_timestamp_count": 0,
        "nonmonotonic_timestamp_count": 0,
        "out_of_bounds_timestamp_count": 0,
        "nonpositive_duration_count": 0,
    }
    values.update(updates)
    return MlxVadTimestampEvidence(**values)


def _text(**updates: float | int) -> MlxVadTextEvidence:
    values: dict[str, float | int] = {
        "wer": 0.05,
        "cer": 0.02,
        "substitutions": 4,
        "deletions": 1,
        "insertions": 0,
        "adjacent_repeated_ngram_token_count": 0,
        "expected_boundary_count": 0,
        "audited_boundary_count": 0,
        "boundary_changed_word_count": 0,
        "boundary_lost_word_count": 0,
        "boundary_duplicate_word_count": 0,
        "boundary_timestamp_gap_count": 0,
        "boundary_timestamp_overlap_count": 0,
    }
    values.update(updates)
    return MlxVadTextEvidence(**values)


def _run(
    variant: MlxVadVariant,
    index: int,
    *,
    status: MlxVadRunStatus = MlxVadRunStatus.COMPLETED,
    status_reason: MlxVadFailureCode | None = None,
    source_config_sha256: str | None = None,
    raw_transcript_sha256: str | None = None,
    asr_seconds: float | None = None,
    total_seconds: float | None = None,
    peak_rss_bytes: int = 1_000,
    peak_mlx_allocator_bytes: int = 2_000,
    timestamps: MlxVadTimestampEvidence | None = None,
    text: MlxVadTextEvidence | None = None,
    binding_overrides: dict[str, str] | None = None,
    whole_file_fallback_occurred: bool = False,
) -> MlxVadRunEvidence:
    candidate = variant is MlxVadVariant.VAD_WINDOWED
    bindings = {
        "input_media_sha256": _hash("1"),
        "canonical_audio_sha256": _hash("2"),
        "vad_artifact_sha256": _hash("3"),
        "reference_sha256": _hash("4"),
        "code_revision": f"{'c' * 40}+source-sha256:{_hash('d')}",
        "plan_binding_sha256": _hash("0"),
        "machine_binding_sha256": _hash("d"),
        "host_condition_binding_sha256": _hash("e"),
        "comparison_config_sha256": _hash("5"),
        "configuration_evidence_sha256": _hash("f"),
        "run_metadata_sha256": _hash("6"),
        "completion_timing_sha256": _hash("7"),
        "quality_evidence_sha256": _hash("8"),
        "boundary_evidence_sha256": _hash("9"),
    }
    bindings.update(binding_overrides or {})
    completed = status is MlxVadRunStatus.COMPLETED
    if not completed:
        for key in (
            "canonical_audio_sha256",
            "vad_artifact_sha256",
            "reference_sha256",
            "comparison_config_sha256",
            "configuration_evidence_sha256",
            "run_metadata_sha256",
            "completion_timing_sha256",
            "quality_evidence_sha256",
            "boundary_evidence_sha256",
        ):
            bindings[key] = None
    return MlxVadRunEvidence(
        run_id=f"{variant.value}-{index}",
        variant=variant,
        sequence_index=index * 2 + (1 if candidate else 0),
        status=status,
        status_reason=status_reason,
        **bindings,
        model=MlxVadModelBinding(
            model_id="mlx-community/whisper-large-v3-mlx",
            revision="pinned-revision",
            checkpoint_sha256=_hash("a"),
            backend_version="0.4.3",
            mlx_version="0.31.2",
        ),
        device=MlxVadDeviceEvidence(),
        source_config_sha256=(
            source_config_sha256
            or (_hash("2") if candidate else _hash("1"))
        )
        if completed
        else None,
        raw_transcript_sha256=(
            raw_transcript_sha256
            or (_hash("b") if candidate else _hash("a"))
        )
        if completed
        else None,
        canonical_transcript_result_sha256=(
            raw_transcript_sha256
            or (_hash("b") if candidate else _hash("a"))
        )
        if completed
        else None,
        mlx_vad_windowed=candidate,
        inference_mode=(
            "mlx"
            if not candidate or whole_file_fallback_occurred
            else "mlx-vad-windowed"
        )
        if completed
        else None,
        raw_mlx_vad_windowed=(
            candidate and not whole_file_fallback_occurred
        )
        if completed
        else None,
        vad_window_count=(
            0 if not candidate or whole_file_fallback_occurred else 4
        )
        if completed
        else None,
        ownership_intervals_non_overlapping=(
            True if candidate and not whole_file_fallback_occurred else None
        ),
        whole_file_fallback_occurred=whole_file_fallback_occurred,
        timestamps=(timestamps or _timestamps()) if completed else None,
        text=(
                text
                or _text(
                    expected_boundary_count=(
                        3
                        if candidate and not whole_file_fallback_occurred
                        else 0
                    ),
                    audited_boundary_count=(
                        3
                        if candidate and not whole_file_fallback_occurred
                        else 0
                    ),
                )
        )
        if completed
        else None,
        performance=MlxVadPerformanceEvidence(
            asr_seconds=asr_seconds
            if asr_seconds is not None
            else (8.0 if candidate else 10.0),
            total_seconds=total_seconds
            if total_seconds is not None
            else (11.0 if candidate else 12.0),
            peak_rss_bytes=peak_rss_bytes,
            peak_mlx_allocator_bytes=peak_mlx_allocator_bytes,
        )
        if completed
        else None,
    )


def _evidence(
    *,
    baseline_runs: int = 3,
    candidate_runs: int = 3,
    reference_reviewed: bool = True,
    candidate_factory=None,
) -> MlxVadCanaryEvidence:
    factory = candidate_factory or (
        lambda index: _run(MlxVadVariant.VAD_WINDOWED, index)
    )
    return MlxVadCanaryEvidence(
        baseline_runs=tuple(
            _run(MlxVadVariant.WHOLE_FILE, index)
            for index in range(baseline_runs)
        ),
        candidate_runs=tuple(factory(index) for index in range(candidate_runs)),
        warmup_runs=(
            MlxVadWarmupEvidence(
                run_id="warmup-whole",
                variant=MlxVadVariant.WHOLE_FILE,
                plan_binding_sha256=_hash("0"),
                artifact_sha256=_hash("1"),
            ),
            MlxVadWarmupEvidence(
                run_id="warmup-windowed",
                variant=MlxVadVariant.VAD_WINDOWED,
                plan_binding_sha256=_hash("0"),
                artifact_sha256=_hash("2"),
            ),
        ),
        reference_reviewed=reference_reviewed,
    )


def test_complete_same_bound_quality_preserving_speedup_passes() -> None:
    decision = evaluate_mlx_vad_gate(_evidence())

    assert decision.outcome is MlxVadCanaryOutcome.PASS
    assert decision.automatic_promotion_eligible is False
    assert (
        "per_media_pass_only_requires_representative_suite_and_trusted_collector"
        in decision.reasons
    )
    assert decision.asr_speedup_ratio == pytest.approx(0.2)
    assert decision.baseline.measured_run_count == 3
    assert decision.candidate.measured_run_count == 3


def test_insufficient_runs_and_unreviewed_reference_are_inconclusive() -> None:
    decision = evaluate_mlx_vad_gate(
        _evidence(
            baseline_runs=1,
            candidate_runs=1,
            reference_reviewed=False,
        )
    )

    assert decision.outcome is MlxVadCanaryOutcome.INCONCLUSIVE
    assert "insufficient_baseline_runs:1<3" in decision.reasons
    assert "insufficient_candidate_runs:1<3" in decision.reasons
    assert "reference_not_reviewed" in decision.reasons


def test_binding_or_reproducibility_drift_is_inconclusive() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            raw_transcript_sha256=_hash("c") if index == 2 else _hash("b"),
            binding_overrides={
                "canonical_audio_sha256": _hash("d"),
            },
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.INCONCLUSIVE
    assert "binding_mismatch:canonical_audio_sha256" in decision.reasons
    assert "candidate_transcript_not_reproducible" in decision.reasons


def test_windowed_whole_file_fallback_is_a_hard_failure() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            whole_file_fallback_occurred=index == 1,
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_whole_file_fallback:vad_windowed-1" in decision.reasons


def test_invalid_candidate_timestamps_fail() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            timestamps=_timestamps(out_of_bounds_timestamp_count=1)
            if index == 0
            else None,
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_timestamp_invalid:vad_windowed-0" in decision.reasons


def test_text_repetition_and_boundary_loss_regressions_fail() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            text=_text(
                wer=0.08,
                cer=0.03,
                insertions=2,
                adjacent_repeated_ngram_token_count=6,
                expected_boundary_count=3,
                audited_boundary_count=3,
                boundary_changed_word_count=2,
                boundary_lost_word_count=1,
            ),
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_boundary_audit_failed" in decision.reasons
    assert "candidate_median_wer_regressed" in decision.reasons
    assert "candidate_insertions_regressed" in decision.reasons
    assert "candidate_repetition_regressed" in decision.reasons


def test_no_material_runtime_or_memory_gain_fails() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            asr_seconds=9.5,
            peak_rss_bytes=1_000,
            peak_mlx_allocator_bytes=2_000,
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "no_material_runtime_or_memory_improvement" in decision.reasons


def test_mlx_allocator_regression_fails_independently_of_process_rss() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            asr_seconds=8.0,
            peak_rss_bytes=1_000,
            peak_mlx_allocator_bytes=2_200,
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_median_peak_mlx_allocator_regressed" in decision.reasons
    assert (
        "candidate_peak_mlx_allocator_outlier_exceeded_baseline_envelope"
        in decision.reasons
    )


def test_total_runtime_regression_fails_even_when_asr_is_faster() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            asr_seconds=8.0,
            total_seconds=13.0,
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_median_total_time_regressed" in decision.reasons
    assert "candidate_total_time_outlier_exceeded_baseline_envelope" in decision.reasons


def test_boundary_timestamp_gap_fails() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            text=_text(
                expected_boundary_count=3,
                audited_boundary_count=3,
                boundary_timestamp_gap_count=1,
            ),
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.FAIL
    assert "candidate_boundary_audit_failed" in decision.reasons


def test_schema_rejects_any_delta_other_than_windowing() -> None:
    with pytest.raises(ValidationError, match="permits exactly"):
        MlxVadCanaryEvidence(
            baseline_runs=(),
            candidate_runs=(),
            warmup_runs=(),
            config_delta_paths=(
                "asr.mlx_vad_windowed",
                "asr.mlx_vad_window_seconds",
            ),
        )


def test_failed_run_requires_typed_reason() -> None:
    with pytest.raises(ValidationError, match="require status_reason"):
        _run(
            MlxVadVariant.VAD_WINDOWED,
            0,
            status=MlxVadRunStatus.FAILED,
        )


def test_incomplete_boundary_audit_is_inconclusive_not_pass() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        return _run(
            MlxVadVariant.VAD_WINDOWED,
            index,
            text=_text(
                expected_boundary_count=3,
                audited_boundary_count=0,
            ),
        )

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.INCONCLUSIVE
    assert decision.automatic_promotion_eligible is False
    assert "candidate_boundary_audit_incomplete:vad_windowed-0" in decision.reasons


def test_failed_run_needs_no_result_artifacts_and_uses_typed_reason() -> None:
    run = _run(
        MlxVadVariant.VAD_WINDOWED,
        0,
        status=MlxVadRunStatus.FAILED,
        status_reason=MlxVadFailureCode.TRANSCRIPTION_FAILED,
    )

    assert run.raw_transcript_sha256 is None
    assert run.canonical_transcript_result_sha256 is None
    assert run.run_metadata_sha256 is None
    assert run.status_reason is MlxVadFailureCode.TRANSCRIPTION_FAILED


def test_completed_run_requires_metric_bearing_artifact_hashes() -> None:
    payload = _run(MlxVadVariant.WHOLE_FILE, 0).model_dump(mode="python")
    payload["completion_timing_sha256"] = None

    with pytest.raises(ValidationError, match="metric-bearing artifact hash"):
        MlxVadRunEvidence.model_validate(payload)


def test_execution_device_and_model_bindings_are_typed() -> None:
    with pytest.raises(ValidationError, match="mlx:gpu"):
        MlxVadDeviceEvidence(runtime_device="cpu")

    with pytest.raises(ValidationError, match="match pattern"):
        MlxVadModelBinding(
            model_id="mlx-model",
            revision="revision",
            checkpoint_sha256="not-a-hash",
            backend_version="0.4.3",
            mlx_version="0.31.2",
        )


def test_measured_schedule_must_alternate_variants() -> None:
    baseline = tuple(
        _run(MlxVadVariant.WHOLE_FILE, index) for index in range(3)
    )
    candidate = tuple(
        _run(MlxVadVariant.VAD_WINDOWED, index) for index in range(3)
    )
    candidate_payload = candidate[0].model_dump(mode="python")
    candidate_payload["sequence_index"] = 6
    candidate = (
        MlxVadRunEvidence.model_validate(candidate_payload),
        *candidate[1:],
    )

    with pytest.raises(ValidationError, match="must alternate"):
        MlxVadCanaryEvidence(
            baseline_runs=baseline,
            candidate_runs=candidate,
            warmup_runs=_evidence().warmup_runs,
            reference_reviewed=True,
        )


def test_privacy_sensitive_free_form_identifiers_are_rejected() -> None:
    payload = _run(MlxVadVariant.WHOLE_FILE, 0).model_dump(mode="python")
    payload["run_id"] = "/private/path/call.wav"
    payload["code_revision"] = "private transcript phrase"

    with pytest.raises(ValidationError):
        MlxVadRunEvidence.model_validate(payload)


def test_volatile_artifact_hashes_may_change_when_canonical_result_is_stable() -> None:
    def candidate(index: int) -> MlxVadRunEvidence:
        run = _run(MlxVadVariant.VAD_WINDOWED, index)
        payload = run.model_dump(mode="python")
        payload["raw_transcript_sha256"] = _hash(str(index))
        payload["run_metadata_sha256"] = _hash(str(index + 3))
        payload["completion_timing_sha256"] = _hash(str(index + 6))
        return MlxVadRunEvidence.model_validate(payload)

    decision = evaluate_mlx_vad_gate(_evidence(candidate_factory=candidate))

    assert decision.outcome is MlxVadCanaryOutcome.PASS


def test_impossible_total_duration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be shorter"):
        MlxVadPerformanceEvidence(
            asr_seconds=10.0,
            total_seconds=1.0,
            peak_rss_bytes=1_000,
            peak_mlx_allocator_bytes=2_000,
        )


def test_warmups_are_required_for_both_variants() -> None:
    evidence = _evidence()
    payload = evidence.model_dump(mode="python")
    payload["warmup_runs"] = payload["warmup_runs"][:1]

    with pytest.raises(ValidationError, match="warmup is required per variant"):
        MlxVadCanaryEvidence.model_validate(payload)


def test_generic_comparison_csv_has_one_status_column(tmp_path) -> None:
    path = tmp_path / "comparison.csv"

    write_comparison_csv(path, {"matched_files": []})

    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header.count("status") == 1
