from dataclasses import replace

import pytest

from scryntic.jobs.admission import LoadingRequirements


def test_reviewed_cpu_loading_is_explicit_and_offline() -> None:
    loading = LoadingRequirements(
        runtime="cpython-3.12-cpu-v1",
        format="safetensors",
        code="reviewed-adapter",
        max_memory_bytes=4 * 1024**3,
    )
    loading.require_safe()
    for changed in (
        replace(loading, network=True),
        replace(loading, downloads=True),
        replace(loading, format="pickle"),
        replace(loading, runtime="unreviewed"),
        replace(loading, max_memory_bytes=8 * 1024**3),
    ):
        with pytest.raises(ValueError):
            changed.require_safe()
