# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import json
import os
from pathlib import Path
from types import SimpleNamespace

from typer.main import get_command
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.schemas import Transcript
from local_transcript_max.speaker_id.identity_io import IdentityResolverError
from local_transcript_max.speaker_id.identity_models import ResolverMode


def _successful_result(mode: ResolverMode = ResolverMode.REVIEW) -> SimpleNamespace:
    report = SimpleNamespace(
        mode=mode,
        run_id="run:cli-test",
        cluster_decisions=[object(), object()],
        turn_decisions=[object()],
        warnings=[],
        validation_summary=SimpleNamespace(valid=True),
    )
    return SimpleNamespace(
        report=report,
        written_artifacts=(
            "speaker_identity_report.json",
            "speaker_identity_attribution_audit.json",
        ),
    )


def test_resolve_identities_help_exposes_frozen_surface() -> None:
    result = CliRunner().invoke(
        app,
        ["speakers", "resolve-identities", "--help"],
        terminal_width=240,
    )

    assert result.exit_code == 0
    root_command = get_command(app)
    speakers_command = root_command.commands["speakers"]
    resolver_command = speakers_command.commands["resolve-identities"]
    exposed_options = {
        option for parameter in resolver_command.params for option in getattr(parameter, "opts", ())
    }
    for option in (
        "--mode",
        "--roster",
        "--known-speakers",
        "--sibling-job",
        "--sibling-root",
        "--sibling-manifest",
        "--external-transcript",
        "--approved-map",
        "--write-identity-mapped-exports",
        "--identity-deliverable-format",
        "--audit-csv",
        "--stability-config",
        "--write-media-review-queue",
        "--media-review-report",
        "--unattributed-label",
        "--min-apply-confidence",
        "--profile-threshold",
        "--profile-ambiguity-margin",
        "--max-fragment-duration",
        "--max-fragment-words",
        "--max-sibling-jobs",
        "--source-media-review",
        "--turn-boundary-repair",
        "--machine-readable",
    ):
        assert option in exposed_options


def test_resolve_identities_maps_all_options_without_printing_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_resolve(job_dir, options):
        captured["job_dir"] = job_dir
        captured["options"] = options
        return _successful_result(ResolverMode.APPLY_SAFE)

    monkeypatch.setattr(
        "local_transcript_max.speaker_id.identity_resolver.resolve_identities",
        fake_resolve,
    )
    private_root = tmp_path / "private-person-name"
    job = private_root / "job"
    roster = private_root / "roster.json"
    sibling_one = private_root / "sibling-one"
    sibling_two = private_root / "sibling-two"
    result = CliRunner().invoke(
        app,
        [
            "speakers",
            "resolve-identities",
            str(job),
            "--mode",
            "apply-safe",
            "--roster",
            str(roster),
            "--known-speakers",
            "2",
            "--sibling-job",
            str(sibling_one),
            "--sibling-job",
            str(sibling_two),
            "--sibling-root",
            str(private_root / "siblings"),
            "--sibling-manifest",
            str(private_root / "manifest.json"),
            "--external-transcript",
            str(private_root / "external.md"),
            "--write-identity-mapped-exports",
            "--identity-deliverable-format",
            "all",
            "--no-audit-csv",
            "--stability-config",
            str(private_root / "stability.json"),
            "--write-media-review-queue",
            "--unattributed-label",
            "Not attributed",
            "--min-apply-confidence",
            "0.96",
            "--profile-threshold",
            "0.82",
            "--profile-ambiguity-margin",
            "0.08",
            "--max-fragment-duration",
            "3.5",
            "--max-fragment-words",
            "7",
            "--max-sibling-jobs",
            "9",
            "--source-media-review",
            "none",
            "--turn-boundary-repair",
            "label-only",
        ],
    )

    assert result.exit_code == 0
    assert captured["job_dir"] == job
    options = captured["options"]
    assert options.known_speakers == 2
    assert options.roster_path == roster
    assert options.sibling_jobs == (sibling_one, sibling_two)
    assert options.write_identity_mapped_exports is True
    assert options.write_media_review_queue is True
    assert options.policy.mode == ResolverMode.APPLY_SAFE
    assert options.policy.audit_csv is False
    assert options.policy.deliverable_format.value == "all"
    assert options.policy.source_media_review.value == "none"
    assert options.policy.turn_boundary_repair.value == "label-only"
    assert options.policy.min_apply_confidence == 0.96
    assert options.policy.profile_threshold == 0.82
    assert options.policy.profile_ambiguity_margin == 0.08
    assert options.policy.max_fragment_duration == 3.5
    assert options.policy.max_fragment_words == 7
    assert options.policy.max_sibling_jobs == 9
    assert str(private_root) not in result.stdout


def test_resolve_identities_machine_result_is_compact_and_path_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "local_transcript_max.speaker_id.identity_resolver.resolve_identities",
        lambda *_args, **_kwargs: _successful_result(),
    )
    private_path = tmp_path / "private-name" / "job"

    result = CliRunner().invoke(
        app,
        ["speakers", "resolve-identities", str(private_path), "--machine-readable"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "speaker_identity_cli_result.v1"
    assert payload["mode"] == "review"
    assert payload["validation_valid"] is True
    assert str(private_path) not in result.stdout


def test_resolve_identities_machine_error_is_typed_and_path_safe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise IdentityResolverError(
            "missing_job",
            "JOB_DIR does not exist.",
            "Choose an existing completed local job.",
        )

    monkeypatch.setattr(
        "local_transcript_max.speaker_id.identity_resolver.resolve_identities",
        fail,
    )
    private_path = tmp_path / "private-name" / "missing-job"

    result = CliRunner().invoke(
        app,
        ["speakers", "resolve-identities", str(private_path), "--machine-readable"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload == {
        "code": "missing_job",
        "message": "JOB_DIR does not exist.",
        "remediation": "Choose an existing completed local job.",
        "safe_context": {"mode": "review"},
        "schema_version": "speaker_identity_error.v1",
    }
    assert str(private_path) not in result.stderr


def test_resolve_identities_invalid_mode_is_machine_readable(tmp_path: Path) -> None:
    private_path = tmp_path / "private-name" / "job"

    result = CliRunner().invoke(
        app,
        [
            "speakers",
            "resolve-identities",
            str(private_path),
            "--mode",
            "automatic",
            "--machine-readable",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["code"] == "invalid_mode"
    assert str(private_path) not in result.stderr


def test_transcribe_identity_flags_populate_opt_in_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"synthetic")
    roster = tmp_path / "roster.json"
    external = tmp_path / "external.md"
    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio),
            "--out",
            str(tmp_path / "out"),
            "--known-speakers",
            "2",
            "--speaker-identity-resolver",
            "review",
            "--speaker-roster",
            str(roster),
            "--speaker-identity-external-transcript",
            str(external),
            "--write-identity-mapped-exports",
            "--identity-deliverable-format",
            "all",
        ],
    )

    assert result.exit_code == 0
    config = captured["config"].speaker_identity
    assert config.mode == "review"
    assert config.known_speakers == 2
    assert config.roster_path == roster
    assert config.external_transcript == external
    assert config.write_identity_mapped_exports is True
    assert config.deliverable_format == "all"


def test_transcribe_identity_defaults_do_not_opt_in(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"synthetic")

    result = CliRunner().invoke(
        app,
        ["transcribe", str(audio), "--out", str(tmp_path / "out")],
    )

    assert result.exit_code == 0
    assert captured["config"].speaker_identity.mode == "off"
    assert captured["config"].speaker_identity.write_identity_mapped_exports is False


def test_transcribe_config_file_identity_values_survive_cli_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"synthetic")
    roster = tmp_path / "roster.json"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "speaker_identity:",
                "  mode: review",
                "  known_speakers: 3",
                f"  roster_path: {roster}",
                "  write_identity_mapped_exports: true",
                "  deliverable_format: all",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio),
            "--config",
            str(config_file),
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"].speaker_identity
    assert config.mode == "review"
    assert config.known_speakers == 3
    assert config.roster_path == roster
    assert config.write_identity_mapped_exports is True
    assert config.deliverable_format == "all"


def test_transcribe_help_narrows_unsupported_nemo_surface() -> None:
    result = CliRunner().invoke(app, ["transcribe", "--help"])

    assert result.exit_code == 0
    assert "nemo-sortformer" not in result.stdout


def test_inspect_reports_identity_artifact_names_and_count_without_job_path(
    tmp_path: Path,
) -> None:
    job = tmp_path / "private-name" / "job"
    job.mkdir(parents=True, mode=0o700)
    for artifact, content in (
        ("speaker_identity_report.json", "{}"),
        ("transcript.identity_mapped.srt", "synthetic"),
    ):
        path = job / artifact
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)

    result = CliRunner().invoke(app, ["inspect", str(job)])

    assert result.exit_code == 0
    assert "speaker_identity_report.json=present" in result.stdout
    assert "transcript.identity_mapped.srt=present" in result.stdout
    assert "identity_artifact_count=2" in result.stdout
    assert str(job) not in result.stdout


def test_serve_marks_non_loopback_runtime_for_hardened_api(monkeypatch) -> None:
    captured = {}
    monkeypatch.delenv("LTM_API_NETWORK_BOUND", raising=False)

    def fake_run(app_path, *, host, port):
        captured["app_path"] = app_path
        captured["host"] = host
        captured["port"] = port
        captured["network_bound"] = os.environ.get("LTM_API_NETWORK_BOUND")

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = CliRunner().invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--allow-network-bind"],
    )

    assert result.exit_code == 0
    assert captured["network_bound"] == "1"
    assert "LTM_API_NETWORK_BOUND" not in os.environ
