import json

import pytest

from scryntic.application.operations import (
    MAX_RESULT_BYTES,
    ApplicationError,
    ErrorCode,
    Operation,
)
from scryntic.workflows.wire import encode_error, encode_result, result


def test_stable_envelope_and_fixed_errors() -> None:
    value = result(Operation.STATUS, {"b": 2, "a": True}, complete=False)
    assert (
        encode_result(value)
        == b'{"complete":false,"data":{"a":true,"b":2},"ok":true,"operation":"status","version":1}'
    )
    assert (
        json.loads(encode_error(ApplicationError(ErrorCode.BUSY)))["error"]["code"]
        == "resource_busy"
    )
    assert ApplicationError(ErrorCode.CANCELLED).exit_code == 130


def test_complete_envelope_is_bounded() -> None:
    with pytest.raises(ApplicationError):
        result(Operation.STATUS, {"value": "x" * MAX_RESULT_BYTES})
    with pytest.raises(ApplicationError):
        result(Operation.STATUS, {"value": float("nan")})


def test_workflow_registry_is_complete_and_closed() -> None:
    from scryntic.use_cases import available_workflows

    workflows = available_workflows()
    assert len(workflows) == len(Operation)
    assert {workflow.operation for workflow in workflows} == set(Operation)
    for workflow in workflows:
        with pytest.raises(ApplicationError):
            workflow.request({"unknown": "SENTINEL_SECRET"})
