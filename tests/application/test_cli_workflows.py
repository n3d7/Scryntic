import json
import logging
import sys

import pytest

from scryntic.cli import main


def test_malformed_cli_is_fixed_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--secret=SENTINEL_SECRET_RAW"]) == 2
    output = capsys.readouterr()
    assert "SENTINEL" not in output.out + output.err
    assert json.loads(output.err)["error"]["code"] == "invalid_request"


def test_cli_suppresses_untrusted_dependency_logging(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scryntic.application.operations import ApplicationResult, Request
    from scryntic.configuration.paths import Installation
    from scryntic.workflows.wire import result

    class Services:
        def __init__(self, installation: Installation) -> None:
            pass

        async def execute(self, request: Request) -> ApplicationResult:
            logging.getLogger("untrusted.dependency").warning("SENTINEL_SECRET_RAW")
            return result(request.operation, {"observation": "offline"})

        async def cancel(self) -> None:
            pass

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr("scryntic.cli.ApplicationServices", Services)
    dependency = logging.getLogger("untrusted.dependency")
    handler = logging.StreamHandler(sys.stderr)
    dependency.addHandler(handler)
    try:
        assert main(["status"]) == 0
    finally:
        dependency.removeHandler(handler)
    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out + captured.err
    assert json.loads(captured.out)["ok"] is True


def test_cli_field_validation_does_not_echo_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["dataset", "inspect", "--manifest-sha256", "SENTINEL_SECRET_RAW"]) == 2
    output = capsys.readouterr()
    assert "SENTINEL" not in output.out + output.err
    assert json.loads(output.err)["ok"] is False


def test_legacy_global_options_are_not_silently_ignored_by_new_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--epoch", "operator-request", "collect", "--profile", "fixture"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["code"] == "invalid_request"
