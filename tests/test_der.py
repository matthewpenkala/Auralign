# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

from pathlib import Path

import pytest

from local_transcript_max.benchmark import der as der_module
from local_transcript_max.benchmark.der import (
    DER_CONTRACT_V1,
    DERStatus,
    RTTMValidationError,
    der_from_rttm,
    score_rttm,
)


def rttm_line(
    speaker: str,
    start: float,
    duration: float,
    *,
    uri: str = "recording",
    channel: int = 1,
) -> str:
    return f"SPEAKER {uri} {channel} {start} {duration} <NA> <NA> {speaker} <NA> <NA>"


def write_rttm(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def test_contract_is_explicit_and_missing_paths_remain_compatible(tmp_path: Path) -> None:
    existing = write_rttm(tmp_path / "existing.rttm", rttm_line("speaker", 0.0, 1.0))

    missing_reference = score_rttm(None, existing)
    missing_hypothesis = score_rttm(existing, tmp_path / "missing.rttm")

    assert DER_CONTRACT_V1.collar_seconds == 0.0
    assert DER_CONTRACT_V1.overlap_scored is True
    assert DER_CONTRACT_V1.evaluation_region == "union"
    assert DER_CONTRACT_V1.speaker_mapping == "optimal_one_to_one"
    assert missing_reference.status is DERStatus.MISSING_REFERENCE
    assert missing_hypothesis.status is DERStatus.MISSING_HYPOTHESIS
    assert der_from_rttm(None, existing) is None
    assert der_from_rttm(existing, None) is None


def test_semantic_order_whitespace_uri_and_speaker_permutation_equivalence(
    tmp_path: Path,
) -> None:
    reference = write_rttm(
        tmp_path / "reference.rttm",
        rttm_line("A", 0.0, 1.0, uri="reference-name"),
        rttm_line("B", 1.0, 1.0, uri="reference-name"),
    )
    hypothesis = write_rttm(
        tmp_path / "hypothesis.rttm",
        "   " + rttm_line("X", 1.0, 1.0, uri="different-name") + "  ",
        "\t".join(rttm_line("Y", 0.0, 1.0, uri="different-name").split()),
    )

    result = score_rttm(reference, hypothesis)

    assert result.status is DERStatus.OK
    assert result.der == 0.0
    assert {
        (mapping.hypothesis_speaker, mapping.reference_speaker) for mapping in result.mapping
    } == {("X", "B"), ("Y", "A")}
    assert result.to_metadata()["contract"] == DER_CONTRACT_V1.to_metadata()


@pytest.mark.parametrize(
    ("reference_lines", "hypothesis_lines", "expected_der", "component", "seconds"),
    [
        ([rttm_line("A", 0.0, 2.0)], [rttm_line("X", 0.0, 1.0)], 0.5, "miss", 1.0),
        (
            [rttm_line("A", 0.0, 1.0)],
            [rttm_line("X", 0.0, 2.0)],
            1.0,
            "false_alarm",
            1.0,
        ),
        (
            [rttm_line("A", 0.0, 1.0), rttm_line("B", 1.0, 1.0)],
            [rttm_line("X", 0.0, 2.0)],
            0.5,
            "confusion",
            1.0,
        ),
    ],
)
def test_individual_error_components(
    tmp_path: Path,
    reference_lines: list[str],
    hypothesis_lines: list[str],
    expected_der: float,
    component: str,
    seconds: float,
) -> None:
    reference = write_rttm(tmp_path / "reference.rttm", *reference_lines)
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", *hypothesis_lines)

    result = score_rttm(reference, hypothesis)

    assert result.der == pytest.approx(expected_der)
    values = {
        "miss": result.missed_speaker_seconds,
        "false_alarm": result.false_alarm_speaker_seconds,
        "confusion": result.speaker_confusion_seconds,
    }
    assert values[component] == seconds
    assert sum(value for key, value in values.items() if key != component) == 0.0


def test_overlap_is_scored_in_reference_speaker_seconds(tmp_path: Path) -> None:
    reference = write_rttm(
        tmp_path / "reference.rttm",
        rttm_line("A", 0.0, 2.0),
        rttm_line("B", 1.0, 1.0),
    )
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", rttm_line("X", 0.0, 2.0))

    result = score_rttm(reference, hypothesis)

    assert result.reference_speaker_seconds == 3.0
    assert result.scored_region_seconds == 2.0
    assert result.missed_speaker_seconds == 1.0
    assert result.speaker_confusion_seconds == 0.0
    assert result.der == pytest.approx(1.0 / 3.0)


def test_union_region_counts_hypothesis_only_speech_as_false_alarm(tmp_path: Path) -> None:
    reference = write_rttm(tmp_path / "reference.rttm", rttm_line("A", 1.0, 1.0))
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", rttm_line("X", 0.0, 3.0))

    result = score_rttm(reference, hypothesis)

    assert result.scored_region_seconds == 3.0
    assert result.reference_speaker_seconds == 1.0
    assert result.false_alarm_speaker_seconds == 2.0
    assert result.der == 2.0


def test_exact_assignment_finds_global_optimum_that_greedy_misses(tmp_path: Path) -> None:
    reference = write_rttm(
        tmp_path / "reference.rttm",
        rttm_line("A", 0.0, 5.0),
        rttm_line("B", 5.0, 4.0),
        rttm_line("A", 9.0, 4.0),
    )
    hypothesis = write_rttm(
        tmp_path / "hypothesis.rttm",
        rttm_line("X", 0.0, 9.0),
        rttm_line("Y", 9.0, 4.0),
    )

    result = score_rttm(reference, hypothesis)

    assert result.der == pytest.approx(5.0 / 13.0)
    assert result.speaker_confusion_seconds == 5.0
    assert [
        (mapping.hypothesis_speaker, mapping.reference_speaker) for mapping in result.mapping
    ] == [("X", "B"), ("Y", "A")]


def test_empty_reference_speech_never_reports_false_zero(tmp_path: Path) -> None:
    reference = write_rttm(tmp_path / "reference.rttm")
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", rttm_line("X", 0.0, 2.0))

    result = score_rttm(reference, hypothesis)

    assert result.status is DERStatus.NO_REFERENCE_SPEECH
    assert result.der is None
    assert result.false_alarm_speaker_seconds == 2.0
    assert der_from_rttm(reference, hypothesis) is None


def test_empty_hypothesis_is_a_complete_miss(tmp_path: Path) -> None:
    reference = write_rttm(tmp_path / "reference.rttm", rttm_line("A", 0.0, 2.0))
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm")

    result = score_rttm(reference, hypothesis)

    assert result.status is DERStatus.OK
    assert result.missed_speaker_seconds == 2.0
    assert result.der == 1.0


@pytest.mark.parametrize(
    "invalid_text",
    [
        "SPEAKER too few fields\n",
        "LEXEME file 1 0 1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER <NA> 1 0 1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 0 0 1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 NaN 1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 0 inf <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 -0.1 1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 0 -1 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 0 0 <NA> <NA> A <NA> <NA>\n",
        "SPEAKER file 1 0 1 <NA> <NA> <NA> <NA> <NA>\n",
        "SPEAKER file 1 1e308 1e308 <NA> <NA> A <NA> <NA>\n",
        ("SPEAKER first 1 0 1 <NA> <NA> A <NA> <NA>\nSPEAKER second 1 1 1 <NA> <NA> B <NA> <NA>\n"),
    ],
)
def test_malformed_nonfinite_negative_and_multirecording_inputs_are_rejected(
    tmp_path: Path, invalid_text: str
) -> None:
    reference = tmp_path / "reference.rttm"
    reference.write_text(invalid_text, encoding="utf-8")
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", rttm_line("X", 0.0, 1.0))

    with pytest.raises(RTTMValidationError, match="reference RTTM"):
        score_rttm(reference, hypothesis)


def test_segment_and_speaker_resource_limits_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hypothesis = write_rttm(tmp_path / "hypothesis.rttm", rttm_line("X", 0.0, 1.0))
    reference = write_rttm(
        tmp_path / "reference.rttm",
        rttm_line("A", 0.0, 1.0),
        rttm_line("B", 1.0, 1.0),
    )
    monkeypatch.setattr(der_module, "MAX_RTTM_SEGMENTS", 1)
    with pytest.raises(RTTMValidationError, match="segment safety limit"):
        score_rttm(reference, hypothesis)

    monkeypatch.setattr(der_module, "MAX_RTTM_SEGMENTS", 100_000)
    monkeypatch.setattr(der_module, "MAX_RTTM_SPEAKERS", 1)
    with pytest.raises(RTTMValidationError, match="speaker safety limit"):
        score_rttm(reference, hypothesis)
