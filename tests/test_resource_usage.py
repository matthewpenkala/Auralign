# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from types import SimpleNamespace

from local_transcript_max import resource_usage
from local_transcript_max.resource_usage import (
    ResourceMonitor,
    framework_allocator_counters,
)


def test_resource_monitor_records_measurement_sources(monkeypatch) -> None:
    monkeypatch.setattr(resource_usage, "current_rss_bytes", lambda pid: 1024)
    monkeypatch.setattr(resource_usage, "process_lifetime_peak_rss_bytes", lambda: 2048)
    monkeypatch.setattr(resource_usage, "process_cpu_seconds", lambda: (1.0, 0.5))
    monitor = ResourceMonitor(interval_seconds=10)
    monitor.start()
    summary = monitor.stop()

    assert summary["rss_sampling_source"] == "native_process_api"
    assert summary["rss_peak_sampled_source"] == "native_process_api"
    assert summary["rss_peak_process_lifetime_source"] == "native_process_api"
    assert summary["cpu_seconds_source"] == "native_process_api"
    assert summary["measurement_notes"] == []


def test_resource_monitor_records_unavailable_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(resource_usage, "current_rss_bytes", lambda pid: None)
    monkeypatch.setattr(resource_usage, "process_lifetime_peak_rss_bytes", lambda: None)
    monkeypatch.setattr(resource_usage, "process_cpu_seconds", lambda: (None, None))
    monitor = ResourceMonitor(interval_seconds=10)
    monitor.start()
    summary = monitor.stop()

    assert summary["sample_count"] == 0
    assert summary["rss_sampling_source"] == "unavailable"
    assert summary["rss_peak_sampled_source"] == "unavailable"
    assert summary["rss_peak_process_lifetime_source"] == "unavailable"
    assert summary["cpu_seconds_source"] == "unavailable"
    assert len(summary["measurement_notes"]) == 3


def test_framework_allocator_counters_are_overlap_labeled(monkeypatch) -> None:
    modules = __import__("sys").modules
    monkeypatch.setitem(
        modules,
        "mlx.core",
        SimpleNamespace(
            get_active_memory=lambda: 10,
            get_peak_memory=lambda: 20,
            get_cache_memory=lambda: 5,
        ),
    )
    monkeypatch.setitem(
        modules,
        "torch",
        SimpleNamespace(
            mps=SimpleNamespace(
                current_allocated_memory=lambda: 30,
                driver_allocated_memory=lambda: 40,
                recommended_max_memory=lambda: 50,
            )
        ),
    )

    counters = framework_allocator_counters()

    assert counters["mlx"]["peak_memory_bytes"] == 20
    assert counters["torch_mps"]["driver_allocated_memory_bytes"] == 40
    assert "do not sum" in counters["measurement_note"]
