# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.platform_security import private_path_is_secure
from local_transcript_max.utils import atomic_text_write, atomic_text_writer, write_json


def test_atomic_text_writer_preserves_existing_file_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("complete\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="interrupted"):
        with atomic_text_writer(target) as handle:
            handle.write("partial")
            raise RuntimeError("interrupted")

    assert target.read_text(encoding="utf-8") == "complete\n"
    assert not list(tmp_path.glob(".artifact.txt.*"))


def test_atomic_writers_publish_private_complete_files(tmp_path: Path) -> None:
    text_path = tmp_path / "artifact.txt"
    json_path = tmp_path / "artifact.json"

    atomic_text_write(text_path, "complete\n")
    write_json(json_path, {"status": "complete"})

    assert text_path.read_text(encoding="utf-8") == "complete\n"
    assert json_path.read_text(encoding="utf-8").endswith("\n")
    assert private_path_is_secure(text_path)
    assert private_path_is_secure(json_path)


def test_atomic_json_rejects_non_finite_values_without_partial_output(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"

    with pytest.raises(ValueError):
        write_json(target, {"invalid": float("nan")})

    assert not target.exists()
    assert not list(tmp_path.glob(".artifact.json.*"))
