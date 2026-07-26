# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Contracts for the reachable-history privacy scanner."""

from __future__ import annotations

import importlib.util
from pathlib import Path, PurePosixPath
from types import ModuleType


def _load_checker() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "check_reachable_history.py"
    spec = importlib.util.spec_from_file_location("check_reachable_history", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_reviewed_swift_models_namespace_is_not_a_cache_path() -> None:
    for path in (
        "macos/Auralign/Models/ReadinessProjection.swift",
        "macos/Auralign/Models/ModelDeliveryCatalog.swift",
        "macos/Auralign/Models/ModelPackStore.swift",
    ):
        assert checker.has_prohibited_path_component(PurePosixPath(path)) is False


def test_other_model_cache_paths_remain_prohibited() -> None:
    for path in (
        "models/weights.bin",
        "macos/Auralign/Models/weights.safetensors",
        "macos/Auralign/Models/Nested/AnotherSource.swift",
        "macos/Auralign/Models/README.md",
        "src/models/ReadinessProjection.swift",
    ):
        assert checker.has_prohibited_path_component(PurePosixPath(path)) is True


def test_immutable_scanner_blob_allows_only_its_private_key_sentinels() -> None:
    object_id = "39a39501ec422d15cd23e4ca4ce37d2e3f817bfb"
    sentinel = b"BEGIN OPENSSH" + b" PRIVATE KEY"

    assert checker.has_unreviewed_secret_shape(object_id, sentinel) is False
    assert checker.has_unreviewed_secret_shape("0" * 40, sentinel) is True
    assert (
        checker.has_unreviewed_secret_shape(
            object_id,
            sentinel + b"\nhf_" + b"a" * 26,
        )
        is True
    )


def test_token_shapes_require_a_real_credential_boundary() -> None:
    assert (
        checker.has_unreviewed_secret_shape(
            "0" * 40,
            b"Asterisk-linking-protocols-exception",
        )
        is False
    )
    assert (
        checker.has_unreviewed_secret_shape(
            "0" * 40,
            b"base64prefixaAKIAAQAAAAAABAAHAM0A",
        )
        is False
    )
    assert (
        checker.has_unreviewed_secret_shape(
            "0" * 40,
            b"token=hf_" + b"a" * 26,
        )
        is True
    )
