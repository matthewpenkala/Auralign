# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from local_transcript_max.benchmark.apple_silicon_canary import (
    AlignmentQAEvidence,
    AppleSiliconCanaryEvidence,
    ArtifactHashBinding,
    CanaryBinding,
    CanaryGatePolicy,
    CanaryOutcome,
    CanaryRun,
    CanaryRunPhase,
    CanaryRunStatus,
    CanaryTimingEvidence,
    CanaryVariant,
    DeviceEvidence,
    ModelVersionBinding,
    PipelineDeviceEvidence,
    RuntimeVersionBinding,
    SpeakerQAEvidence,
    evaluate_mps_diarization_gate,
)


def _hash(character: str) -> str:
    return character * 64


def _binding() -> CanaryBinding:
    return CanaryBinding(
        input_media_sha256=_hash("a"),
        comparison_config_sha256=_hash("b"),
        speaker_constraints_sha256=_hash("c"),
        machine_fingerprint_sha256=_hash("d"),
        runtime_environment_sha256=_hash("7"),
        code_revision="4329325714cba150e1e4a2a52a772ea6671bd31e",
        models=(
            ModelVersionBinding(
                role="asr",
                model_id="mlx-community/whisper-large-v3-mlx",
                revision=None,
                revision_verified=False,
                checkpoint_sha256=_hash("8"),
                backend="mlx-whisper",
                backend_version="0.4.3",
            ),
            ModelVersionBinding(
                role="alignment",
                model_id="facebook/wav2vec2-base-960h",
                revision="22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
                backend="whisperx",
                backend_version="3.8.6",
            ),
            ModelVersionBinding(
                role="diarization",
                model_id="pyannote/speaker-diarization-community-1",
                revision="community-1-revision",
                backend="pyannote",
                backend_version="4.0.3",
            ),
        ),
        runtime_versions=(
            RuntimeVersionBinding(package="mlx", version="0.31.2"),
            RuntimeVersionBinding(package="mlx-metal", version="0.31.2"),
            RuntimeVersionBinding(package="mlx-whisper", version="0.4.3"),
            RuntimeVersionBinding(package="torch", version="2.8.0"),
            RuntimeVersionBinding(package="torchaudio", version="2.8.0"),
            RuntimeVersionBinding(package="whisperx", version="3.8.6"),
            RuntimeVersionBinding(package="pyannote.audio", version="4.0.4"),
        ),
    )


def _devices(
    variant: CanaryVariant,
    *,
    alignment_device: str = "cpu",
    diarization_actual: str | None = None,
    fallback: bool = False,
) -> PipelineDeviceEvidence:
    expected_diarization = (
        "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    )
    actual = "cpu" if fallback else (diarization_actual or expected_diarization)
    resolved = "cpu" if fallback else expected_diarization
    return PipelineDeviceEvidence(
        asr=DeviceEvidence(requested="auto", resolved="mlx", actual="mlx:gpu"),
        alignment=DeviceEvidence(
            requested=alignment_device,
            resolved=alignment_device,
            actual=alignment_device,
        ),
        diarization=DeviceEvidence(
            requested=expected_diarization,
            resolved=resolved,
            actual=actual,
            fallback_occurred=fallback,
            fallback_reason="unsupported MPS operation" if fallback else None,
            synchronized_before=(
                True if variant is CanaryVariant.MPS_CANDIDATE and not fallback else False
            ),
            synchronized_after=(
                True if variant is CanaryVariant.MPS_CANDIDATE and not fallback else False
            ),
        ),
    )


def _alignment_qa(**updates: float | int) -> AlignmentQAEvidence:
    values: dict[str, float | int] = {
        "forced_word_coverage": 0.99,
        "estimated_word_coverage": 0.01,
        "missing_timestamp_count": 0,
        "nonmonotonic_timestamp_count": 0,
    }
    values.update(updates)
    return AlignmentQAEvidence.model_validate(values)


def _speaker_qa(
    *,
    requested_speaker_count: int = 2,
    detected_speaker_count: int = 2,
    validated_speaker_count: int = 2,
    uncertain_speaker_rate: float = 0.05,
    unassigned_speaker_rate: float = 0.02,
    speaker_flapping: float = 0.10,
    critical_defects: tuple[str, ...] = (),
    major_defects: tuple[str, ...] = (),
) -> SpeakerQAEvidence:
    return SpeakerQAEvidence(
        requested_speaker_count=requested_speaker_count,
        detected_speaker_count=detected_speaker_count,
        validated_speaker_count=validated_speaker_count,
        uncertain_speaker_rate=uncertain_speaker_rate,
        unassigned_speaker_rate=unassigned_speaker_rate,
        speaker_flapping=speaker_flapping,
        critical_defects=critical_defects,
        major_defects=major_defects,
    )


def _run(
    *,
    variant: CanaryVariant,
    run_id: str,
    phase: CanaryRunPhase,
    diarization_seconds: float,
    total_seconds: float,
    binding: CanaryBinding,
    alignment_qa: AlignmentQAEvidence | None = None,
    speaker_qa: SpeakerQAEvidence | None = None,
    devices: PipelineDeviceEvidence | None = None,
    peak_memory_bytes: int = 1_000_000_000,
) -> CanaryRun:
    return CanaryRun(
        run_id=run_id,
        variant=variant,
        phase=phase,
        status=CanaryRunStatus.COMPLETED,
        binding=binding,
        source_config_sha256=(
            _hash("e") if variant is CanaryVariant.CPU_BASELINE else _hash("f")
        ),
        processing_timestamp="2026-07-18T12:00:00+00:00",
        artifacts=tuple(
            ArtifactHashBinding(name=name, sha256=_hash(character))
            for name, character in (
                ("completion_timing.json", "7"),
                ("run_metadata.json", "1"),
                ("transcript.raw.json", "2"),
                ("alignment_report.json", "3"),
                ("diarization.json", "4"),
                ("qa_report.json", "5"),
                ("speaker_validation_report.json", "6"),
            )
        ),
        devices=devices or _devices(variant),
        timings=CanaryTimingEvidence(
            preparation_seconds=1.0,
            asr_seconds=5.0,
            alignment_seconds=2.0,
            diarization_seconds=diarization_seconds,
            total_seconds=total_seconds,
        ),
        peak_memory_bytes=peak_memory_bytes,
        alignment_qa=alignment_qa or _alignment_qa(),
        speaker_qa=speaker_qa or _speaker_qa(),
    )


def _evidence(
    *,
    baseline_diarization: tuple[float, float, float] = (9.0, 10.0, 11.0),
    candidate_diarization: tuple[float, float, float] = (8.0, 8.5, 9.0),
    baseline_total: tuple[float, float, float] = (19.0, 20.0, 21.0),
    candidate_total: tuple[float, float, float] = (18.0, 19.0, 20.0),
    candidate_alignment_qa: AlignmentQAEvidence | None = None,
    candidate_speaker_qa: SpeakerQAEvidence | None = None,
    binding: CanaryBinding | None = None,
    candidate_binding: CanaryBinding | None = None,
    explicit_mps_override: bool = False,
) -> AppleSiliconCanaryEvidence:
    shared_binding = binding or _binding()
    mps_binding = candidate_binding or shared_binding
    baseline = [
        _run(
            variant=CanaryVariant.CPU_BASELINE,
            run_id="cpu-warmup",
            phase=CanaryRunPhase.WARMUP,
            diarization_seconds=100.0,
            total_seconds=120.0,
            binding=shared_binding,
        )
    ]
    candidate = [
        _run(
            variant=CanaryVariant.MPS_CANDIDATE,
            run_id="mps-warmup",
            phase=CanaryRunPhase.WARMUP,
            diarization_seconds=0.25,
            total_seconds=12.0,
            binding=mps_binding,
        )
    ]
    for index, (diarization, total) in enumerate(
        zip(baseline_diarization, baseline_total, strict=True),
        start=1,
    ):
        baseline.append(
            _run(
                variant=CanaryVariant.CPU_BASELINE,
                run_id=f"cpu-measured-{index}",
                phase=CanaryRunPhase.MEASURED_WARM,
                diarization_seconds=diarization,
                total_seconds=total,
                binding=shared_binding,
                peak_memory_bytes=1_000_000_000 + index,
            )
        )
    for index, (diarization, total) in enumerate(
        zip(candidate_diarization, candidate_total, strict=True),
        start=1,
    ):
        candidate.append(
            _run(
                variant=CanaryVariant.MPS_CANDIDATE,
                run_id=f"mps-measured-{index}",
                phase=CanaryRunPhase.MEASURED_WARM,
                diarization_seconds=diarization,
                total_seconds=total,
                binding=mps_binding,
                alignment_qa=candidate_alignment_qa,
                speaker_qa=candidate_speaker_qa,
                peak_memory_bytes=900_000_000 + index,
            )
        )
    return AppleSiliconCanaryEvidence(
        baseline_runs=tuple(baseline),
        candidate_runs=tuple(candidate),
        explicit_mps_override=explicit_mps_override,
    )


def _replace_run_status(
    run: CanaryRun,
    status: CanaryRunStatus,
    detail: str,
) -> CanaryRun:
    payload = run.model_dump(mode="python")
    payload.update(
        {
            "status": status,
            "timings": None,
            "peak_memory_bytes": None,
            "alignment_qa": None,
            "speaker_qa": None,
            "status_detail": detail,
        }
    )
    return CanaryRun.model_validate(payload)


def test_binding_rejects_missing_model_role() -> None:
    payload = _binding().model_dump(mode="python")
    payload["models"] = [
        row for row in payload["models"] if row["role"] != "alignment"
    ]

    with pytest.raises(ValidationError, match="exact asr, alignment, and diarization"):
        CanaryBinding.model_validate(payload)


def test_binding_rejects_duplicate_model_role_with_distinct_identifier() -> None:
    payload = _binding().model_dump(mode="python")
    duplicate = dict(payload["models"][0])
    duplicate["model_id"] = "mlx-community/alternate-whisper-large-v3"
    payload["models"] = [*payload["models"], duplicate]

    with pytest.raises(ValidationError, match="exact asr, alignment, and diarization"):
        CanaryBinding.model_validate(payload)


def test_binding_rejects_wrong_backend_for_model_role() -> None:
    payload = _binding().model_dump(mode="python")
    for row in payload["models"]:
        if row["role"] == "diarization":
            row["backend"] = "unexpected"

    with pytest.raises(ValidationError, match="backend mismatch"):
        CanaryBinding.model_validate(payload)


def test_binding_rejects_missing_runtime_package() -> None:
    payload = _binding().model_dump(mode="python")
    payload["runtime_versions"] = [
        row
        for row in payload["runtime_versions"]
        if row["package"] != "mlx-metal"
    ]

    with pytest.raises(ValidationError, match="exact reviewed package set"):
        CanaryBinding.model_validate(payload)


def _replace_candidate_runs(
    evidence: AppleSiliconCanaryEvidence,
    transform: Callable[[CanaryRun], CanaryRun],
) -> AppleSiliconCanaryEvidence:
    return AppleSiliconCanaryEvidence(
        baseline_runs=evidence.baseline_runs,
        candidate_runs=tuple(transform(run) for run in evidence.candidate_runs),
        explicit_mps_override=evidence.explicit_mps_override,
    )


def test_outcome_vocabulary_is_exact() -> None:
    assert {outcome.value for outcome in CanaryOutcome} == {
        "cpu_retained",
        "mps_promoted",
        "mps_explicit_override",
        "benchmark_inconclusive",
        "mps_failed",
        "mps_fell_back_to_cpu",
        "qa_regression",
        "insufficient_evidence",
    }


def test_default_gate_promotes_and_retains_complete_individual_evidence() -> None:
    evidence = _evidence()

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.MPS_PROMOTED
    assert decision.automatic_promotion_eligible is True
    assert decision.policy.minimum_measured_runs == 3
    assert decision.policy.minimum_diarization_speedup_ratio == 0.10
    assert decision.baseline.medians is not None
    assert decision.candidate.medians is not None
    assert decision.baseline.medians.diarization_seconds == 10.0
    assert decision.candidate.medians.diarization_seconds == 8.5
    assert decision.baseline.medians.peak_memory_bytes == 1_000_000_002
    assert decision.candidate.medians.peak_memory_bytes == 900_000_002
    assert decision.baseline.excluded_run_ids == ("cpu-warmup",)
    assert decision.candidate.excluded_run_ids == ("mps-warmup",)
    assert decision.baseline.measured_run_ids == (
        "cpu-measured-1",
        "cpu-measured-2",
        "cpu-measured-3",
    )
    payload = decision.model_dump(mode="json")
    candidate_run = payload["evidence"]["candidate_runs"][1]
    assert candidate_run["devices"]["diarization"] == {
        "requested": "mps",
        "resolved": "mps",
        "actual": "mps",
        "fallback_occurred": False,
        "fallback_reason": None,
        "synchronized_before": True,
        "synchronized_after": True,
    }
    assert candidate_run["timings"] == {
        "preparation_seconds": 1.0,
        "asr_seconds": 5.0,
        "alignment_seconds": 2.0,
        "diarization_seconds": 8.0,
        "total_seconds": 18.0,
    }
    assert candidate_run["alignment_qa"]["forced_word_coverage"] == 0.99
    assert candidate_run["speaker_qa"]["validated_speaker_count"] == 2


def test_cold_start_and_warmup_are_both_excluded_from_warm_medians() -> None:
    evidence = _evidence()
    cold = _run(
        variant=CanaryVariant.CPU_BASELINE,
        run_id="cpu-cold",
        phase=CanaryRunPhase.COLD_START,
        diarization_seconds=500.0,
        total_seconds=600.0,
        binding=evidence.baseline_runs[0].binding,
    )
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=(cold, *evidence.baseline_runs),
        candidate_runs=evidence.candidate_runs,
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.baseline.medians is not None
    assert decision.baseline.medians.diarization_seconds == 10.0
    assert decision.baseline.cold_start_run_ids == ("cpu-cold",)
    assert decision.baseline.excluded_run_ids == ("cpu-cold", "cpu-warmup")


def test_exact_speed_and_end_to_end_boundaries_are_inclusive() -> None:
    evidence = _evidence(
        candidate_diarization=(8.0, 9.0, 10.0),
        candidate_total=(20.0, 21.0, 22.0),
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.diarization_speedup_ratio == pytest.approx(0.10)
    assert decision.total_time_regression_ratio == pytest.approx(0.05)
    assert decision.outcome is CanaryOutcome.MPS_PROMOTED


@pytest.mark.parametrize(
    ("candidate_diarization", "candidate_total"),
    [
        ((8.5, 9.0001, 10.0), (19.0, 20.0, 21.0)),
        ((8.0, 9.0, 10.0), (20.0, 21.001, 22.0)),
    ],
)
def test_performance_beyond_an_inclusive_boundary_retains_cpu(
    candidate_diarization: tuple[float, float, float],
    candidate_total: tuple[float, float, float],
) -> None:
    decision = evaluate_mps_diarization_gate(
        _evidence(
            candidate_diarization=candidate_diarization,
            candidate_total=candidate_total,
        )
    )

    assert decision.outcome is CanaryOutcome.CPU_RETAINED
    assert decision.automatic_promotion_eligible is False


def test_single_candidate_total_time_outlier_blocks_median_promotion() -> None:
    decision = evaluate_mps_diarization_gate(
        _evidence(
            candidate_diarization=(8.0, 8.0, 8.0),
            candidate_total=(18.0, 18.0, 100.0),
        )
    )

    assert decision.total_time_regression_ratio == pytest.approx(-0.10)
    assert decision.outcome is CanaryOutcome.CPU_RETAINED
    assert decision.automatic_promotion_eligible is False
    assert any(
        reason.startswith("candidate_total_time_unstable:mps-measured-3:")
        for reason in decision.reasons
    )


def test_configurable_qa_tolerance_boundaries_are_inclusive() -> None:
    candidate_alignment = _alignment_qa(
        forced_word_coverage=0.98,
        estimated_word_coverage=0.02,
        missing_timestamp_count=1,
        nonmonotonic_timestamp_count=1,
    )
    candidate_speaker = _speaker_qa(
        detected_speaker_count=1,
        validated_speaker_count=1,
        uncertain_speaker_rate=0.06,
        unassigned_speaker_rate=0.03,
        speaker_flapping=0.11,
    )
    policy = CanaryGatePolicy(
        maximum_missing_timestamp_increase=1,
        maximum_nonmonotonic_timestamp_increase=1,
        maximum_forced_coverage_regression=0.01,
        maximum_estimated_coverage_increase=0.01,
        maximum_uncertain_speaker_rate_increase=0.01,
        maximum_unassigned_speaker_rate_increase=0.01,
        maximum_speaker_flapping_increase=0.01,
        speaker_count_tolerance=1,
    )

    decision = evaluate_mps_diarization_gate(
        _evidence(
            candidate_alignment_qa=candidate_alignment,
            candidate_speaker_qa=candidate_speaker,
        ),
        policy,
    )

    assert decision.outcome is CanaryOutcome.MPS_PROMOTED


@pytest.mark.parametrize(
    ("alignment_qa", "speaker_qa", "reason"),
    [
        (
            _alignment_qa(missing_timestamp_count=1),
            _speaker_qa(),
            "missing_timestamps_regressed",
        ),
        (
            _alignment_qa(nonmonotonic_timestamp_count=1),
            _speaker_qa(),
            "nonmonotonic_timestamps_regressed",
        ),
        (
            _alignment_qa(forced_word_coverage=0.98),
            _speaker_qa(),
            "forced_alignment_coverage_regressed",
        ),
        (
            _alignment_qa(forced_word_coverage=0.98, estimated_word_coverage=0.02),
            _speaker_qa(),
            "estimated_alignment_coverage_regressed",
        ),
        (
            _alignment_qa(),
            _speaker_qa(uncertain_speaker_rate=0.06),
            "uncertain_speaker_rate_regressed",
        ),
        (
            _alignment_qa(),
            _speaker_qa(unassigned_speaker_rate=0.03),
            "unassigned_speaker_rate_regressed",
        ),
        (
            _alignment_qa(),
            _speaker_qa(speaker_flapping=0.11),
            "speaker_flapping_regressed",
        ),
        (
            _alignment_qa(),
            _speaker_qa(detected_speaker_count=1),
            "detected_speaker_count_unacceptable",
        ),
        (
            _alignment_qa(),
            _speaker_qa(validated_speaker_count=1),
            "validated_speaker_count_unacceptable",
        ),
        (
            _alignment_qa(),
            _speaker_qa(critical_defects=("timestamp_integrity",)),
            "new_critical_qa_defect",
        ),
        (
            _alignment_qa(),
            _speaker_qa(major_defects=("speaker_count",)),
            "new_major_qa_defect",
        ),
    ],
)
def test_each_qa_regression_blocks_promotion(
    alignment_qa: AlignmentQAEvidence,
    speaker_qa: SpeakerQAEvidence,
    reason: str,
) -> None:
    decision = evaluate_mps_diarization_gate(
        _evidence(
            candidate_alignment_qa=alignment_qa,
            candidate_speaker_qa=speaker_qa,
        )
    )

    assert decision.outcome is CanaryOutcome.QA_REGRESSION
    assert any(item.startswith(reason) for item in decision.reasons)


@pytest.mark.parametrize(
    "candidate_binding",
    [
        _binding().model_copy(update={"input_media_sha256": _hash("1")}),
        _binding().model_copy(update={"comparison_config_sha256": _hash("2")}),
        _binding().model_copy(update={"code_revision": "different-code-revision"}),
        _binding().model_copy(
            update={
                "models": (
                    _binding().models[0].model_copy(update={"revision": "different-revision"}),
                    _binding().models[1],
                )
            }
        ),
    ],
    ids=["input", "config", "code", "model"],
)
def test_input_config_model_and_code_binding_mismatches_are_insufficient(
    candidate_binding: CanaryBinding,
) -> None:
    decision = evaluate_mps_diarization_gate(
        _evidence(candidate_binding=candidate_binding)
    )

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    assert any(
        reason.startswith("comparison_binding_mismatch") for reason in decision.reasons
    )


def test_source_config_drift_within_one_variant_is_insufficient() -> None:
    evidence = _evidence()
    changed = evidence.candidate_runs[-1].model_copy(
        update={"source_config_sha256": _hash("9")}
    )
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=evidence.baseline_runs,
        candidate_runs=(*evidence.candidate_runs[:-1], changed),
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    assert "candidate_source_config_changed_between_runs" in decision.reasons


@pytest.mark.parametrize("drop_warmup", [False, True])
def test_run_count_and_warmup_requirements_are_enforced(drop_warmup: bool) -> None:
    evidence = _evidence()
    baseline_runs = evidence.baseline_runs[1:] if drop_warmup else evidence.baseline_runs
    candidate_runs = (
        evidence.candidate_runs[1:]
        if drop_warmup
        else evidence.candidate_runs[:-1]
    )
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=baseline_runs,
        candidate_runs=candidate_runs,
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    expected = (
        "insufficient_baseline_warmups"
        if drop_warmup
        else "insufficient_candidate_measured_runs"
    )
    assert any(reason.startswith(expected) for reason in decision.reasons)


def test_candidate_fallback_has_a_distinct_outcome_and_is_retained() -> None:
    evidence = _evidence()
    fallback_run_id = "mps-measured-2"

    def add_fallback(run: CanaryRun) -> CanaryRun:
        if run.run_id != fallback_run_id:
            return run
        return run.model_copy(
            update={"devices": _devices(CanaryVariant.MPS_CANDIDATE, fallback=True)}
        )

    evidence = _replace_candidate_runs(evidence, add_fallback)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.MPS_FELL_BACK_TO_CPU
    retained = next(
        run
        for run in decision.evidence.candidate_runs
        if run.run_id == fallback_run_id
    )
    assert retained.devices.diarization.fallback_occurred is True
    assert retained.devices.diarization.actual == "cpu"


def test_failed_candidate_run_has_a_distinct_outcome_and_is_not_hidden() -> None:
    evidence = _evidence()
    failed_id = "mps-measured-3"

    def fail_one(run: CanaryRun) -> CanaryRun:
        if run.run_id == failed_id:
            return _replace_run_status(
                run,
                CanaryRunStatus.FAILED,
                "MPS diarization kernel failed",
            )
        return run

    evidence = _replace_candidate_runs(evidence, fail_one)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.MPS_FAILED
    assert failed_id in decision.candidate.failed_run_ids
    assert any(run.run_id == failed_id for run in decision.evidence.candidate_runs)


def test_common_baseline_and_candidate_failure_is_inconclusive() -> None:
    evidence = _evidence()
    failed_baseline = _replace_run_status(
        evidence.baseline_runs[-1],
        CanaryRunStatus.FAILED,
        "common model preflight failed",
    )
    failed_candidate = _replace_run_status(
        evidence.candidate_runs[-1],
        CanaryRunStatus.FAILED,
        "common model preflight failed",
    )
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=(*evidence.baseline_runs[:-1], failed_baseline),
        candidate_runs=(*evidence.candidate_runs[:-1], failed_candidate),
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert f"baseline_run_failed:{failed_baseline.run_id}" in decision.reasons


def test_inconclusive_run_has_a_distinct_outcome() -> None:
    evidence = _evidence()
    inconclusive = _replace_run_status(
        evidence.baseline_runs[-1],
        CanaryRunStatus.INCONCLUSIVE,
        "thermal throttling invalidated the sample",
    )
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=(*evidence.baseline_runs[:-1], inconclusive),
        candidate_runs=evidence.candidate_runs,
    )

    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert inconclusive.run_id in decision.baseline.inconclusive_run_ids


def test_explicit_override_is_distinct_and_does_not_claim_automatic_eligibility() -> None:
    decision = evaluate_mps_diarization_gate(
        _evidence(
            candidate_diarization=(9.1, 9.5, 9.9),
            explicit_mps_override=True,
        )
    )

    assert decision.outcome is CanaryOutcome.MPS_EXPLICIT_OVERRIDE
    assert decision.automatic_promotion_eligible is False
    assert any(
        "automatic_outcome=cpu_retained" in reason for reason in decision.reasons
    )


def test_alignment_cpu_invariant_cannot_be_bypassed_by_explicit_override() -> None:
    evidence = _evidence(explicit_mps_override=True)
    changed_id = "mps-measured-1"

    def move_alignment(run: CanaryRun) -> CanaryRun:
        if run.run_id != changed_id:
            return run
        return run.model_copy(
            update={
                "devices": _devices(
                    CanaryVariant.MPS_CANDIDATE,
                    alignment_device="mps",
                )
            }
        )

    evidence = _replace_candidate_runs(evidence, move_alignment)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    assert decision.automatic_promotion_eligible is False
    assert f"alignment_not_fixed_to_cpu:{changed_id}" in decision.reasons


def test_asr_must_remain_on_verified_mlx_gpu_across_variants() -> None:
    evidence = _evidence()

    def move_asr_to_cpu(run: CanaryRun) -> CanaryRun:
        return run.model_copy(
            update={
                "devices": run.devices.model_copy(
                    update={
                        "asr": run.devices.asr.model_copy(
                            update={"actual": "mlx:cpu"}
                        )
                    }
                )
            }
        )

    evidence = _replace_candidate_runs(evidence, move_asr_to_cpu)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    assert decision.automatic_promotion_eligible is False
    assert "asr_device_changed_across_variants" in decision.reasons
    assert all(
        f"candidate_asr_not_verified_mlx_gpu:{run.run_id}" in decision.reasons
        for run in evidence.candidate_runs
    )


def test_candidate_mps_timing_must_be_synchronized() -> None:
    evidence = _evidence()

    def remove_sync(run: CanaryRun) -> CanaryRun:
        diarization = run.devices.diarization.model_copy(
            update={"synchronized_after": False}
        )
        return run.model_copy(
            update={"devices": run.devices.model_copy(update={"diarization": diarization})}
        )

    evidence = _replace_candidate_runs(evidence, remove_sync)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.INSUFFICIENT_EVIDENCE
    assert decision.automatic_promotion_eligible is False
    assert all(
        f"candidate_diarization_not_synchronized:{run.run_id}" in decision.reasons
        for run in evidence.candidate_runs
    )


def test_single_candidate_qa_outlier_blocks_promotion_even_when_median_is_clean() -> None:
    evidence = _evidence()
    outlier_id = "mps-measured-1"

    def add_alignment_outlier(run: CanaryRun) -> CanaryRun:
        if run.run_id != outlier_id:
            return run
        return run.model_copy(
            update={
                "alignment_qa": _alignment_qa(
                    missing_timestamp_count=10,
                )
            }
        )

    evidence = _replace_candidate_runs(evidence, add_alignment_outlier)
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.candidate.medians is not None
    assert decision.candidate.medians.missing_timestamp_count == 0.0
    assert decision.outcome is CanaryOutcome.QA_REGRESSION
    assert any(
        reason.startswith(f"missing_timestamps_regressed:{outlier_id}:")
        for reason in decision.reasons
    )


def test_baseline_speaker_count_mismatch_blocks_promotion() -> None:
    evidence = _evidence()

    def invalidate_baseline_count(run: CanaryRun) -> CanaryRun:
        if run.phase is not CanaryRunPhase.MEASURED_WARM:
            return run
        return run.model_copy(
            update={
                "speaker_qa": _speaker_qa(
                    requested_speaker_count=2,
                    detected_speaker_count=1,
                    validated_speaker_count=1,
                )
            }
        )

    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=tuple(
            invalidate_baseline_count(run) for run in evidence.baseline_runs
        ),
        candidate_runs=evidence.candidate_runs,
    )
    decision = evaluate_mps_diarization_gate(evidence)

    assert decision.outcome is CanaryOutcome.QA_REGRESSION
    assert decision.automatic_promotion_eligible is False
    assert all(
        f"detected_speaker_count_unacceptable:{run.run_id}" in decision.reasons
        and f"validated_speaker_count_unacceptable:{run.run_id}" in decision.reasons
        for run in evidence.baseline_runs
        if run.phase is CanaryRunPhase.MEASURED_WARM
    )


def test_completed_runs_require_requested_resolved_and_actual_device_evidence() -> None:
    evidence = _evidence()
    run = evidence.candidate_runs[-1]
    payload = run.model_dump(mode="python")
    payload["devices"]["diarization"]["actual"] = None

    with pytest.raises(ValidationError, match="resolved and actual diarization devices"):
        CanaryRun.model_validate(payload)
