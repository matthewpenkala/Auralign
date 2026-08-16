# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_installed_model_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep unit tests independent of models installed on the host Mac."""

    from local_transcript_max import models as models_module

    system_root = (
        tmp_path.parent
        / f"{tmp_path.name}-system-model-root"
        / "com.auralign.studio.models.bootstrap.v2"
    )
    user_packs_root = tmp_path.parent / f"{tmp_path.name}-user-model-packs"
    verify_installed_pack = models_module._verify_installed_model_pack_once
    verify_packaged_bundle = models_module._verify_packaged_model_bundle_once
    monkeypatch.setattr(models_module, "INSTALLED_MODEL_BUNDLE_ROOT", system_root)
    monkeypatch.setattr(models_module, "INSTALLED_MODEL_PACKS_ROOT", user_packs_root)
    verify_installed_pack.cache_clear()
    verify_packaged_bundle.cache_clear()
    yield
    verify_installed_pack.cache_clear()
    verify_packaged_bundle.cache_clear()
