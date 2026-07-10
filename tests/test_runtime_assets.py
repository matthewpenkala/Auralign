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
    PUNKT_FILES,
    configure_offline_environment,
    prepare_generated_punkt,
    punkt_language_dir,
    verify_generated_punkt,
)


def install_fake_nltk(monkeypatch: pytest.MonkeyPatch) -> None:
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
        for index, name in enumerate(PUNKT_FILES):
            (root / name).write_text(f"generated-{index}\n", encoding="utf-8")

    punkt.PunktParameters = PunktParameters  # type: ignore[attr-defined]
    punkt.save_punkt_params = save_punkt_params  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nltk", nltk)
    monkeypatch.setitem(sys.modules, "nltk.tokenize", tokenize)
    monkeypatch.setitem(sys.modules, "nltk.tokenize.punkt", punkt)
    monkeypatch.setattr(
        "importlib.metadata.version", lambda name: "test" if name == "nltk" else "0"
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

    (punkt_language_dir(tmp_path) / PUNKT_FILES[0]).write_text("tampered", encoding="utf-8")
    with pytest.raises(BackendUnavailableError, match="integrity"):
        verify_generated_punkt(tmp_path)


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
