# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from types import SimpleNamespace

from local_transcript_max.model_lifecycle import release_runtime_models


class ReleasableBackend:
    def __init__(self) -> None:
        self.released = False

    def clear_runtime_cache(self) -> None:
        self.released = True


def test_per_job_release_clears_backends_and_loaded_framework_caches(
    monkeypatch,
) -> None:
    backend = ReleasableBackend()
    calls: list[str] = []
    fake_mlx = SimpleNamespace(clear_cache=lambda: calls.append("mlx"))
    fake_mps = SimpleNamespace(empty_cache=lambda: calls.append("torch_mps"))
    monkeypatch.setitem(__import__("sys").modules, "mlx.core", fake_mlx)
    monkeypatch.setitem(
        __import__("sys").modules,
        "torch",
        SimpleNamespace(mps=fake_mps),
    )

    evidence = release_runtime_models((backend, None, object()))

    assert backend.released is True
    assert evidence["policy"] == "per-job"
    assert evidence["released_backends"] == ["ReleasableBackend"]
    assert evidence["mlx_allocator_cache_cleared"] is True
    assert evidence["torch_mps_allocator_cache_cleared"] is True
    assert calls == ["mlx", "torch_mps"]
