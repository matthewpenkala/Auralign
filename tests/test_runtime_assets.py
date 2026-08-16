# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.runtime_assets import (
    EMPTY_FILE_SHA256,
    PUNKT_FILES,
    PUNKT_MARKER,
    PUNKT_NLTK_VERSION,
    configure_offline_environment,
    prepare_generated_punkt,
    punkt_language_dir,
    verify_generated_punkt,
)
from local_transcript_max.utils import read_json, write_json


def install_fake_nltk(
    monkeypatch: pytest.MonkeyPatch, *, version: str = PUNKT_NLTK_VERSION
) -> None:
    nltk = ModuleType("nltk")
    nltk.__path__ = []  # type: ignore[attr-defined]
    nltk.data = SimpleNamespace(path=[])  # type: ignore[attr-defined]
    tokenize = ModuleType("nltk.tokenize")
    tokenize.__path__ = []  # type: ignore[attr-defined]
    punkt = ModuleType("nltk.tokenize.punkt")

    class PunktParameters:
        pass

    def save_punkt_params(_params: object, *, dir: str) -> None:
        root = Path(dir)
        root.mkdir(parents=True, exist_ok=True)
        for name in PUNKT_FILES:
            (root / name).write_text("", encoding="utf-8")

    punkt.PunktParameters = PunktParameters  # type: ignore[attr-defined]
    punkt.save_punkt_params = save_punkt_params  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nltk", nltk)
    monkeypatch.setitem(sys.modules, "nltk.tokenize", tokenize)
    monkeypatch.setitem(sys.modules, "nltk.tokenize.punkt", punkt)
    monkeypatch.setattr(
        "importlib.metadata.version", lambda name: version if name == "nltk" else "0"
    )


def test_generated_punkt_is_deterministic_and_integrity_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_nltk(monkeypatch)
    first = prepare_generated_punkt(tmp_path)
    second = verify_generated_punkt(tmp_path)

    assert first == second
    assert first["training_data"] == "none"
    assert set(first["files"]) == set(PUNKT_FILES)
    assert set(first["files"].values()) == {EMPTY_FILE_SHA256}
    nltk = sys.modules["nltk"]
    nltk.data.path.extend(["/user/nltk_data", str(tmp_path / "runtime-assets" / "nltk_data")])
    verify_generated_punkt(tmp_path)
    assert nltk.data.path[0] == str(tmp_path / "runtime-assets" / "nltk_data")
    assert nltk.data.path.count(str(tmp_path / "runtime-assets" / "nltk_data")) == 1

    (punkt_language_dir(tmp_path) / PUNKT_FILES[0]).write_text("tampered", encoding="utf-8")
    with pytest.raises(BackendUnavailableError, match="integrity"):
        verify_generated_punkt(tmp_path)


@pytest.mark.parametrize("field", ["generator", "training_data", "nltk_version"])
def test_generated_punkt_rejects_self_attested_or_stale_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    install_fake_nltk(monkeypatch)
    prepare_generated_punkt(tmp_path)
    marker = tmp_path / "runtime-assets" / "nltk_data" / PUNKT_MARKER
    payload = read_json(marker)
    payload[field] = "unreviewed"
    write_json(marker, payload)

    with pytest.raises(BackendUnavailableError, match="locked local generator"):
        verify_generated_punkt(tmp_path)


def test_generated_punkt_rejects_nonempty_files_even_with_matching_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_nltk(monkeypatch)
    prepare_generated_punkt(tmp_path)
    language_dir = punkt_language_dir(tmp_path)
    changed = language_dir / PUNKT_FILES[0]
    changed.write_text("upstream-data", encoding="utf-8")
    marker = tmp_path / "runtime-assets" / "nltk_data" / PUNKT_MARKER
    payload = read_json(marker)
    payload["files"][PUNKT_FILES[0]] = (
        __import__("hashlib").sha256(changed.read_bytes()).hexdigest()
    )
    write_json(marker, payload)

    with pytest.raises(BackendUnavailableError, match="manifest"):
        verify_generated_punkt(tmp_path)


def test_generated_punkt_rejects_unlocked_nltk_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_nltk(monkeypatch, version="0.0.0")

    with pytest.raises(BackendUnavailableError, match="locked corpus-free Punkt"):
        prepare_generated_punkt(tmp_path)


def test_offline_environment_disables_network_capable_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PYANNOTE_METRICS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    configure_offline_environment(nltk_data=tmp_path)

    assert __import__("os").environ["HF_HUB_OFFLINE"] == "1"
    assert __import__("os").environ["TRANSFORMERS_OFFLINE"] == "1"
    assert __import__("os").environ["PYANNOTE_METRICS_ENABLED"] == "false"
    assert __import__("os").environ["NLTK_DATA"] == str(tmp_path)
