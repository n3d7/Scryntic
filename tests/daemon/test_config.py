import os
from pathlib import Path

import pytest

from scryntic.configuration.paths import Installation
from scryntic.daemon.config import DaemonConfig


def installation(root: Path) -> Installation:
    paths = (root / "config", root / "state", root / "runtime")
    for path in (*paths, paths[0] / "credentials"):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    uid = os.geteuid()
    return Installation(*paths, paths[0] / "credentials", uid, uid, False)


def test_config_keeps_explicit_profile_and_restart_identity(tmp_path: Path) -> None:
    config = DaemonConfig(installation(tmp_path), profile="fixture")
    assert config.profile == "fixture"
    assert config.producer == "daemon"
    assert config.epoch == "daemon-v1"


@pytest.mark.parametrize(
    "values",
    [
        {"profile": "automatic"},
        {"queue_capacity": 0},
        {"queue_capacity": 4097},
        {"storage_reserve_bytes": 0},
        {"pipeline_interval_s": float("nan")},
        {"clock_interval_s": 0},
        {"symbol": "../credentials"},
        {"category": "arbitrary"},
    ],
)
def test_config_rejects_invalid_runtime_bounds(
    tmp_path: Path, values: dict[str, object]
) -> None:
    target = installation(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        DaemonConfig(target, **values)  # type: ignore[arg-type]
