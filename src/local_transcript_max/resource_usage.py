# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Lightweight local process resource sampling."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from .platform_security import current_process_usage


class ResourceMonitor:
    """Sample current-process RSS without adding a runtime dependency."""

    def __init__(self, *, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = max(0.1, interval_seconds)
        self.pid = os.getpid()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_count = 0
        self._rss_start_bytes: int | None = None
        self._rss_end_bytes: int | None = None
        self._rss_peak_bytes: int | None = None
        self._started_at: float | None = None
        self._ended_at: float | None = None
        self._stage_samples: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._started_at = time.perf_counter()
        self._sample()
        self._thread = threading.Thread(target=self._run, name="ltm-resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
            self._thread = None
        self._sample()
        self._ended_at = time.perf_counter()
        return self.summary()

    def snapshot(self, stage: str) -> dict[str, Any]:
        """Capture a privacy-safe process sample at a named stage boundary."""

        rss = current_rss_bytes(self.pid)
        user_cpu_seconds, system_cpu_seconds = process_cpu_seconds()
        sample = {
            "stage": stage,
            "elapsed_seconds": (
                round(time.perf_counter() - self._started_at, 6)
                if self._started_at is not None
                else None
            ),
            "rss_bytes": rss,
            "user_cpu_seconds": user_cpu_seconds,
            "system_cpu_seconds": system_cpu_seconds,
            "framework_memory": framework_allocator_counters(),
        }
        self._stage_samples.append(sample)
        return dict(sample)

    def summary(self) -> dict[str, Any]:
        rss_values = [] if self._rss_start_bytes is None else [self._rss_start_bytes]
        peak_ru = process_lifetime_peak_rss_bytes()
        user_cpu_seconds, system_cpu_seconds = process_cpu_seconds()
        cpu_available = user_cpu_seconds is not None or system_cpu_seconds is not None
        return {
            "pid": self.pid,
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": self._sample_count,
            "rss_sampling_source": "native_process_api" if self._sample_count else "unavailable",
            "rss_start_bytes": self._rss_start_bytes,
            "rss_end_bytes": self._rss_end_bytes,
            "rss_peak_sampled_bytes": self._rss_peak_bytes,
            "rss_peak_sampled_source": (
                "native_process_api" if self._sample_count else "unavailable"
            ),
            "rss_peak_process_lifetime_bytes": peak_ru,
            "rss_peak_process_lifetime_source": (
                "native_process_api" if peak_ru is not None else "unavailable"
            ),
            "user_cpu_seconds": user_cpu_seconds,
            "system_cpu_seconds": system_cpu_seconds,
            "cpu_seconds_source": "native_process_api" if cpu_available else "unavailable",
            "monitor_duration_seconds": (
                round(self._ended_at - self._started_at, 6)
                if self._started_at is not None and self._ended_at is not None
                else None
            ),
            "stage_samples": list(self._stage_samples),
            "framework_memory_final": framework_allocator_counters(),
            "measurement_notes": resource_measurement_notes(rss_values, peak_ru, cpu_available),
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        rss = current_rss_bytes(self.pid)
        if rss is None:
            return
        self._sample_count += 1
        if self._rss_start_bytes is None:
            self._rss_start_bytes = rss
        self._rss_end_bytes = rss
        self._rss_peak_bytes = (
            rss if self._rss_peak_bytes is None else max(self._rss_peak_bytes, rss)
        )


def current_rss_bytes(pid: int | None = None) -> int | None:
    if pid is not None and pid != os.getpid():
        return None
    try:
        return current_process_usage().resident_bytes
    except OSError:
        return None


def process_lifetime_peak_rss_bytes() -> int | None:
    try:
        value = current_process_usage().peak_resident_bytes
    except OSError:
        return None
    if value <= 0:
        return None
    return value


def process_cpu_seconds() -> tuple[float | None, float | None]:
    try:
        usage = current_process_usage()
        return usage.user_seconds, usage.system_seconds
    except OSError:
        return None, None


def framework_allocator_counters() -> dict[str, Any]:
    """Sample already-loaded accelerator allocators without importing frameworks.

    MLX, Torch MPS, and RSS describe overlapping unified-memory views. Consumers
    must compare them independently and never add them together.
    """

    counters: dict[str, Any] = {
        "measurement_note": (
            "Framework allocator counters overlap with process RSS and with each "
            "other on unified memory; do not sum them."
        )
    }
    mlx_core = sys.modules.get("mlx.core")
    if mlx_core is not None:
        counters["mlx"] = {
            "active_memory_bytes": _optional_counter(mlx_core, "get_active_memory"),
            "peak_memory_bytes": _optional_counter(mlx_core, "get_peak_memory"),
            "cache_memory_bytes": _optional_counter(mlx_core, "get_cache_memory"),
        }
    torch = sys.modules.get("torch")
    mps = getattr(torch, "mps", None) if torch is not None else None
    if mps is not None:
        counters["torch_mps"] = {
            "current_allocated_memory_bytes": _optional_counter(
                mps,
                "current_allocated_memory",
            ),
            "driver_allocated_memory_bytes": _optional_counter(
                mps,
                "driver_allocated_memory",
            ),
            "recommended_max_memory_bytes": _optional_counter(
                mps,
                "recommended_max_memory",
            ),
        }
    return counters


def _optional_counter(source: object, name: str) -> int | None:
    function = getattr(source, name, None)
    if not callable(function):
        return None
    try:
        return int(function())
    except (RuntimeError, TypeError, ValueError):
        return None


def resource_measurement_notes(
    rss_values: list[int],
    peak_ru: int | None,
    cpu_available: bool,
) -> list[str]:
    notes: list[str] = []
    if not rss_values:
        notes.append("sampled RSS unavailable from the native process API")
    if peak_ru is None:
        notes.append("process resident memory unavailable from the native process API")
    if not cpu_available:
        notes.append("process CPU seconds unavailable from the native process API")
    return notes
