# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

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
