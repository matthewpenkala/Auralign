# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Lightweight local process resource sampling."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any


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

    def summary(self) -> dict[str, Any]:
        rss_values = [] if self._rss_start_bytes is None else [self._rss_start_bytes]
        peak_ru = process_lifetime_peak_rss_bytes()
        user_cpu_seconds, system_cpu_seconds = process_cpu_seconds()
        cpu_available = user_cpu_seconds is not None or system_cpu_seconds is not None
        return {
            "pid": self.pid,
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": self._sample_count,
            "rss_sampling_source": "ps" if self._sample_count else "unavailable",
            "rss_start_bytes": self._rss_start_bytes,
            "rss_end_bytes": self._rss_end_bytes,
            "rss_peak_sampled_bytes": self._rss_peak_bytes,
            "rss_peak_sampled_source": "ps" if self._sample_count else "unavailable",
            "rss_peak_process_lifetime_bytes": peak_ru,
            "rss_peak_process_lifetime_source": (
                "resource.getrusage" if peak_ru is not None else "unavailable"
            ),
            "user_cpu_seconds": user_cpu_seconds,
            "system_cpu_seconds": system_cpu_seconds,
            "cpu_seconds_source": "resource.getrusage" if cpu_available else "unavailable",
            "monitor_duration_seconds": (
                round(self._ended_at - self._started_at, 6)
                if self._started_at is not None and self._ended_at is not None
                else None
            ),
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
    pid = pid or os.getpid()
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            capture_output=True,
            check=False,
        )
        value = completed.stdout.strip().split()[0]
        return int(value) * 1024
    except (IndexError, OSError, ValueError):
        return None


def process_lifetime_peak_rss_bytes() -> int | None:
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    if value <= 0:
        return None
    return value if sys.platform == "darwin" else value * 1024


def process_cpu_seconds() -> tuple[float | None, float | None]:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        return float(usage.ru_utime), float(usage.ru_stime)
    except Exception:
        return None, None


def resource_measurement_notes(
    rss_values: list[int],
    peak_ru: int | None,
    cpu_available: bool,
) -> list[str]:
    notes: list[str] = []
    if not rss_values:
        notes.append("sampled RSS unavailable; install/provide ps-compatible process tools")
    if peak_ru is None:
        notes.append("process lifetime peak RSS unavailable from resource.getrusage")
    if not cpu_available:
        notes.append("process CPU seconds unavailable from resource.getrusage")
    return notes
