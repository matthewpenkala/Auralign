# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max import code_provenance


def test_code_revision_binds_commit_and_uncommitted_source_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "src" / "local_transcript_max"
    package.mkdir(parents=True)
    source = package / "runtime.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr(code_provenance, "_git_commit", lambda _root: "a" * 40)

    first = code_provenance.current_code_revision(tmp_path)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = code_provenance.current_code_revision(tmp_path)

    assert first.startswith(f"{'a' * 40}+source-sha256:")
    assert first != second
    assert str(tmp_path) not in first


def test_standalone_runtime_uses_embedded_immutable_build_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "runtime" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    (tmp_path / "runtime" / "release-build.json").write_text(
        (
            '{"schema_version":"auralign-embedded-build.v1",'
            f'"source_revision":"{"b" * 40}",'
            f'"source_tree_sha256":"{"c" * 64}"}}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(code_provenance.sys, "executable", str(executable))

    assert code_provenance.current_code_revision() == (
        f"{'b' * 40}+source-sha256:{'c' * 64}"
    )


def test_malformed_standalone_build_record_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "runtime" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    (tmp_path / "runtime" / "release-build.json").write_text(
        '{"schema_version":"auralign-embedded-build.v1","source_revision":"no"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(code_provenance.sys, "executable", str(executable))

    with pytest.raises(RuntimeError, match="invalid source identity"):
        code_provenance.current_code_revision()
