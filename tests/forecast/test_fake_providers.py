"""Trusted fake providers consume an actual F08 dataset through its reader."""

import asyncio
from pathlib import Path

from scryntic.application.providers import ForecastRequest
from scryntic.composition import build_fake_dataset
from scryntic.configuration.paths import Installation
from scryntic.dataset.snapshot import DatasetReader
from scryntic.providers.fake import PersistenceFakeProvider, TrendFakeProvider
from scryntic.sources.fake import INTERVAL_NS


def _installation(tmp_path: Path) -> Installation:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    installation = Installation.workstation(home=home, environment={})
    for path in (
        installation.config_dir,
        installation.state_dir,
        installation.runtime_dir,
        installation.credential_dir,
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return installation


def test_two_local_fake_providers_are_swappable_on_real_dataset(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    reference = build_fake_dataset(
        installation,
        epoch="providers-a",
        code_revision="f09-test",
        dependency_lock_sha256="d" * 64,
    )
    reader = DatasetReader(installation)
    request = ForecastRequest(
        request_id="f09-provider-test",
        dataset=reference,
        target="close",
        frequency_ns=INTERVAL_NS,
        horizon=2,
    )
    constant = PersistenceFakeProvider(reader)
    trend = TrendFakeProvider(reader)
    first = asyncio.run(constant.forecast(request))
    second = asyncio.run(trend.forecast(request))
    first.validate_for(request, constant.descriptor)
    second.validate_for(request, trend.descriptor)
    assert [point.value for point in first.points] == [101.25, 101.25]
    assert [point.value for point in second.points] == [101.75, 102.25]
    assert first.points[0].timestamp_ns == second.points[0].timestamp_ns
    assert first.provider_id != second.provider_id
