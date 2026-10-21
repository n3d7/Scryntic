import asyncio
import json
import time
from pathlib import Path

import pytest

from scryntic.application.data_operations import (
    DatasetBuildRequest,
    DatasetExportRequest,
    DatasetInspectRequest,
)
from scryntic.application.operations import ApplicationError
from scryntic.configuration.paths import Installation
from scryntic.research.ledger import ResearchLedger, Reservation
from scryntic.workflows.data import DataWorkflows
from tests.application.test_ml_preparation import setup_policy


def reserve(root: Installation, development_end_ns: int) -> None:
    with ResearchLedger(root) as ledger:
        ledger.reserve(
            Reservation(
                "a" * 64,
                time.time_ns() + 10**15,
                time.time_ns() + 2 * 10**15,
                development_end_ns,
            )
        )


def test_existing_inspection_export_and_raw_build_cannot_expose_reserved_data(
    tmp_path: Path,
) -> None:
    context, manifest = setup_policy(tmp_path)
    flows = DataWorkflows(context)
    built = asyncio.run(flows.execute(DatasetBuildRequest((manifest,))))
    digest = json.loads(built.payload)["dataset"]["manifest_sha256"]
    reserve(context.installation, 1)
    for request in (
        DatasetInspectRequest(digest),
        DatasetExportRequest(digest, "forbidden"),
        DatasetBuildRequest((manifest,)),
    ):
        operation = flows.execute(request)
        with pytest.raises(ApplicationError):
            asyncio.run(operation)
    with ResearchLedger(context.installation) as ledger:
        assert sum(e["kind"] == "access_denied" for e in ledger.events) == 3
