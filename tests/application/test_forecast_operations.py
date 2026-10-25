import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from scripts.verify_cli_install import _installation
from scryntic.application.forecast_operations import (
    ForecastInspectRequest,
    ForecastStatusRequest,
)
from scryntic.cli import main
from scryntic.configuration.paths import Installation
from scryntic.forecast.ledger import ForecastLedger
from scryntic.use_cases import ApplicationServices, available_workflows
from tests.research.test_forecast_ledger import Clock, content


def test_real_cli_and_service_expose_same_immutable_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    _installation(home)
    target = Installation.workstation(home=home, environment={})
    with ForecastLedger(target, Clock()) as ledger:
        expected = ledger.publish(content(), attempt_id="1" * 32)

    async def scenario() -> dict[str, object]:
        service = ApplicationServices(target)
        try:
            status = await service.execute(ForecastStatusRequest())
            assert json.loads(status.payload)["publications"] == 1
            result = await service.execute(
                ForecastInspectRequest(content().slot.sha256)
            )
            return cast(dict[str, object], json.loads(result.payload))
        finally:
            await service.aclose()

    result = asyncio.run(scenario())
    assert result["receipt"] == expected
    assert (
        main(
            [
                "--home",
                str(home),
                "forecasts",
                "inspect",
                "--slot-sha256",
                content().slot.sha256,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"] == result
    assert any(w.operation.value == "forecasts.inspect" for w in available_workflows())
