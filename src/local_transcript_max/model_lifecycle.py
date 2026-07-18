# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Explicit, opt-in release of heavyweight local model and allocator caches."""

from __future__ import annotations

import gc
import sys
from collections.abc import Iterable
from typing import Any


def release_runtime_models(backends: Iterable[object | None]) -> dict[str, Any]:
    """Release backend-owned models for the explicit ``per-job`` policy.

    Framework cache cleanup is deliberately absent from the default lifecycle:
    it can reduce warm throughput. This function is called only when the
    operator selected the low-residency ``per-job`` policy.
    """

    released_backends: list[str] = []
    for backend in backends:
        if backend is None:
            continue
        release = getattr(backend, "clear_runtime_cache", None)
        if not callable(release):
            continue
        try:
            release()
        except Exception:
            continue
        released_backends.append(backend.__class__.__name__)

    collected_objects = gc.collect()
    mlx_cache_cleared = _clear_mlx_allocator_cache()
    torch_mps_cache_cleared = _clear_torch_mps_allocator_cache()
    return {
        "policy": "per-job",
        "released_backends": sorted(set(released_backends)),
        "garbage_collected_objects": collected_objects,
        "mlx_allocator_cache_cleared": mlx_cache_cleared,
        "torch_mps_allocator_cache_cleared": torch_mps_cache_cleared,
        "note": (
            "Explicit per-job cleanup trades warm throughput for lower retained "
            "model and allocator residency."
        ),
    }


def _clear_mlx_allocator_cache() -> bool:
    mlx_core = sys.modules.get("mlx.core")
    clear_cache = getattr(mlx_core, "clear_cache", None) if mlx_core is not None else None
    if not callable(clear_cache):
        return False
    try:
        clear_cache()
        return True
    except RuntimeError:
        return False


def _clear_torch_mps_allocator_cache() -> bool:
    torch = sys.modules.get("torch")
    mps = getattr(torch, "mps", None) if torch is not None else None
    empty_cache = getattr(mps, "empty_cache", None)
    if not callable(empty_cache):
        return False
    try:
        empty_cache()
        return True
    except RuntimeError:
        return False
