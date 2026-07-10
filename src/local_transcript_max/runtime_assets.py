# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic local runtime assets that never require network access."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Any

from .errors import BackendUnavailableError
from .utils import read_json, sha256_file, write_json

PUNKT_MARKER = ".ltm-generated-punkt.json"
PUNKT_FILES = (
    "abbrev_types.txt",
    "collocations.tab",
    "ortho_context.tab",
    "sent_starters.txt",
)


def configure_offline_environment(*, nltk_data: Path | None = None) -> None:
    """Apply fail-closed environment defaults for third-party ML libraries."""

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"
    if nltk_data is not None:
        os.environ["NLTK_DATA"] = str(nltk_data.expanduser())


def punkt_data_root(model_cache: Path) -> Path:
    return model_cache.expanduser() / "runtime-assets" / "nltk_data"


def punkt_language_dir(model_cache: Path) -> Path:
    return punkt_data_root(model_cache) / "tokenizers" / "punkt_tab" / "english"


def prepare_generated_punkt(model_cache: Path) -> dict[str, Any]:
    """Generate corpus-free Punkt parameters from the installed NLTK code."""

    try:
        from nltk.tokenize.punkt import PunktParameters, save_punkt_params
    except Exception as exc:  # pragma: no cover - optional dependency boundary
        raise BackendUnavailableError(
            "NLTK is required by WhisperX forced alignment. Install the locked alignment "
            "profile before preparing runtime assets."
        ) from exc

    root = punkt_data_root(model_cache)
    language_dir = punkt_language_dir(model_cache)
    language_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    save_punkt_params(PunktParameters(), dir=str(language_dir))
    managed_dirs = (
        root,
        root / "tokenizers",
        root / "tokenizers" / "punkt_tab",
        language_dir,
    )
    for path in managed_dirs:
        if path.exists() and path.is_dir():
            try:
                path.chmod(0o700)
            except OSError:
                pass
    for name in PUNKT_FILES:
        path = language_dir / name
        if not path.is_file():
            raise BackendUnavailableError(f"Generated Punkt asset is missing {name}.")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    payload: dict[str, Any] = {
        "schema_version": "ltm-generated-punkt.v1",
        "generator": "nltk.tokenize.punkt.save_punkt_params",
        "nltk_version": importlib.metadata.version("nltk"),
        "training_data": "none",
        "files": {name: sha256_file(language_dir / name) for name in PUNKT_FILES},
    }
    write_json(root / PUNKT_MARKER, payload)
    configure_offline_environment(nltk_data=root)
    _add_nltk_search_path(root)
    return payload


def verify_generated_punkt(model_cache: Path) -> dict[str, Any]:
    """Verify the generated parameters and make them discoverable by WhisperX."""

    root = punkt_data_root(model_cache)
    marker = root / PUNKT_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise BackendUnavailableError(
            "Generated English Punkt parameters are missing. Run the runtime preparation "
            "command while the locked alignment dependencies are installed."
        )
    payload = read_json(marker)
    if not isinstance(payload, dict) or payload.get("schema_version") != "ltm-generated-punkt.v1":
        raise BackendUnavailableError("Generated Punkt provenance marker is invalid.")
    files = payload.get("files")
    if not isinstance(files, dict) or set(files) != set(PUNKT_FILES):
        raise BackendUnavailableError("Generated Punkt file manifest is invalid.")
    language_dir = punkt_language_dir(model_cache)
    for name in PUNKT_FILES:
        path = language_dir / name
        if not path.is_file() or path.is_symlink():
            raise BackendUnavailableError(f"Generated Punkt asset is missing or unsafe: {name}.")
        if sha256_file(path) != files[name]:
            raise BackendUnavailableError(f"Generated Punkt asset failed integrity check: {name}.")
    configure_offline_environment(nltk_data=root)
    _add_nltk_search_path(root)
    return payload


def _add_nltk_search_path(root: Path) -> None:
    try:
        import nltk
    except Exception as exc:  # pragma: no cover - optional dependency boundary
        raise BackendUnavailableError("NLTK is not importable for WhisperX alignment.") from exc
    encoded = str(root)
    if encoded not in nltk.data.path:
        nltk.data.path.insert(0, encoded)
