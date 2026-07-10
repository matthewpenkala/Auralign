# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from local_transcript_max import resource_usage
from local_transcript_max.resource_usage import ResourceMonitor


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
