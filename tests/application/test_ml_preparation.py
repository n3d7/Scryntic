"""ML preparation reuses F18 artifacts and exports the exact source-policy decision."""

import asyncio
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from scryntic.application.data_operations import DatasetBuildRequest
from scryntic.application.dto import BuildDatasetRequest
from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
)
from scryntic.configuration.paths import Installation
from scryntic.dataset.schemas import F18_RECIPE_SCHEMA
from scryntic.dataset.service import DatasetService
from scryntic.imports.catalog import ImportCatalog
from scryntic.workflows.data import DataWorkflows
from tests.application.test_data_operations import Context, accept
from tests.dataset.test_source_usage import policy_document
from tests.imports.test_catalog import published_inputs
from tests.normalization.helpers import installation


def setup_policy(tmp_path: Path, status: str = "permitted") -> tuple[Context, str]:
    data, incoming = published_inputs(tmp_path)
    root = installation(tmp_path / "workstation")
    accepted = accept(root, data, incoming)
    with ImportCatalog(root) as catalog:
        source = catalog.resolve(accepted).body.partition.source
    document = policy_document()
    document["sources"] = [document["sources"][0]]
    document["sources"][0].update(source_id=source, status=status)
    (root.config_dir / "research-policy").write_text(json.dumps(document))
    (root.config_dir / "research-policy").chmod(0o600)
    return Context(root), accepted


def test_inspection_exposes_the_exact_source_key_for_policy_configuration(
    tmp_path: Path,
) -> None:
    from scryntic.application.data_operations import DataInspectRequest

    context, accepted = setup_policy(tmp_path)
    result = asyncio.run(DataWorkflows(context).execute(DataInspectRequest(accepted)))
    with ImportCatalog(context.installation) as catalog:
        expected = catalog.resolve(accepted).body.partition
    assert json.loads(result.payload)["partition"] == {
        "source": expected.source,
        "event_family": expected.event_family,
        "utc_date": expected.utc_date,
    }


def test_preparation_keeps_v1_identity_and_exports_pinned_policy(
    tmp_path: Path,
) -> None:
    context, accepted = setup_policy(tmp_path)
    with ImportCatalog(context.installation) as catalog:
        legacy = (
            DatasetService(
                catalog,
                context.installation,
                code_revision=context.code_revision,
                dependency_lock_sha256=context.dependency_lock_sha256,
            )
            .build(BuildDatasetRequest((accepted,), F18_RECIPE_SCHEMA))
            .dataset
        )
    request = DatasetBuildRequest(
        (accepted,),
        usage_policy_leaf="research-policy",
        preparation_destination="ml-preparation",
    )
    result = asyncio.run(DataWorkflows(context).execute(request))
    body = json.loads(result.payload)
    assert body["dataset"]["manifest_sha256"] == legacy.manifest_sha256
    files = context.exports["ml-preparation"]
    assert set(files) == {
        "dataset.manifest.json",
        "dataset.parquet",
        "source-use-policy.json",
        "preparation.json",
    }
    receipt = json.loads(files["preparation.json"])
    assert receipt["dataset_manifest_sha256"] == legacy.manifest_sha256
    assert receipt["input_manifests"] == [accepted]
    assert receipt["purpose"] == "features-only"
    assert (
        receipt["policy_sha256"] == sha256(files["source-use-policy.json"]).hexdigest()
    )
    assert (
        body["preparation"]["receipt_sha256"]
        == sha256(files["preparation.json"]).hexdigest()
    )
    assert files["dataset.parquet"][:4] == b"PAR1"


@pytest.mark.parametrize("status", ["pending", "denied"])
def test_unpermitted_preparation_stops_before_dataset_writes(
    tmp_path: Path, status: str
) -> None:
    context, accepted = setup_policy(tmp_path, status)
    request = DatasetBuildRequest(
        (accepted,),
        usage_policy_leaf="research-policy",
        preparation_destination="rejected",
    )
    operation = DataWorkflows(context).execute(request)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(operation)
    assert rejected.value.code == ErrorCode.CONFIGURATION
    assert not context.exports
    assert not (context.installation.state_dir / "datasets").exists()


def test_ml_preparation_cannot_derive_labels_before_research_safeguards(
    tmp_path: Path,
) -> None:
    context, accepted = setup_policy(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        DatasetBuildRequest(
            (accepted,),
            label_horizon_steps=1,
            usage_policy_leaf="research-policy",
            preparation_destination="labels",
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"usage_policy_leaf": "research-policy"},
        {"preparation_destination": "output"},
        {"usage_policy_leaf": "../policy", "preparation_destination": "output"},
    ],
)
def test_preparation_needs_protected_policy_and_output_together(
    fields: dict[str, Any],
) -> None:
    with pytest.raises((ValueError, TypeError)):
        DatasetBuildRequest(("a" * 64,), **fields)


def run_real_preparation(
    root: Installation, accepted: str, destination: str
) -> ApplicationResult:
    from scryntic.workflows.context import WorkflowContext

    async def run() -> ApplicationResult:
        context = WorkflowContext(root)
        try:
            return await DataWorkflows(context).execute(
                DatasetBuildRequest(
                    (accepted,),
                    usage_policy_leaf="research-policy",
                    preparation_destination=destination,
                )
            )
        finally:
            await context.aclose()

    return asyncio.run(run())


def test_real_export_is_complete_and_reopens_without_overwriting(
    tmp_path: Path,
) -> None:
    context, accepted = setup_policy(tmp_path)
    first = json.loads(
        run_real_preparation(context.installation, accepted, "first").payload
    )
    output = context.installation.state_dir / "exports/first"
    complete = json.loads((output / "complete.json").read_bytes())
    assert set(complete["sha256"]) == {
        "dataset.manifest.json",
        "dataset.parquet",
        "source-use-policy.json",
        "preparation.json",
    }
    for name, expected in complete["sha256"].items():
        assert sha256((output / name).read_bytes()).hexdigest() == expected
    second = json.loads(
        run_real_preparation(context.installation, accepted, "second").payload
    )
    assert first["dataset"] == second["dataset"]
    assert (
        first["preparation"]["receipt_sha256"]
        == second["preparation"]["receipt_sha256"]
    )
    with pytest.raises(ApplicationError) as conflict:
        run_real_preparation(context.installation, accepted, "first")
    assert conflict.value.code == ErrorCode.BUSY


def test_failed_bundle_is_incomplete_and_restart_can_export_same_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scryntic.workflows.context as context_module

    context, accepted = setup_policy(tmp_path)
    original = context_module._write

    def fail_receipt(parent: int, name: str, data: bytes) -> None:
        if name == "preparation.json":
            raise OSError("injected preparation publication failure")
        original(parent, name, data)

    with monkeypatch.context() as patch:
        patch.setattr(context_module, "_write", fail_receipt)
        with pytest.raises(ApplicationError) as incomplete:
            run_real_preparation(context.installation, accepted, "partial")
    assert incomplete.value.code == ErrorCode.INCOMPLETE
    output = context.installation.state_dir / "exports/partial"
    assert not (output / "complete.json").exists()
    with pytest.raises(ApplicationError) as conflict:
        run_real_preparation(context.installation, accepted, "partial")
    assert conflict.value.code == ErrorCode.BUSY
    recovered = json.loads(
        run_real_preparation(context.installation, accepted, "recovered").payload
    )
    assert (
        recovered["dataset"]["manifest_sha256"]
        == sha256((output / "dataset.manifest.json").read_bytes()).hexdigest()
    )


def test_policy_revocation_is_observed_after_restart(tmp_path: Path) -> None:
    context, accepted = setup_policy(tmp_path)
    run_real_preparation(context.installation, accepted, "permitted")
    path = context.installation.config_dir / "research-policy"
    document = json.loads(path.read_bytes())
    document["sources"][0]["status"] = "denied"
    path.write_text(json.dumps(document))
    with pytest.raises(ApplicationError) as denied:
        run_real_preparation(context.installation, accepted, "revoked")
    assert denied.value.code == ErrorCode.CONFIGURATION
    assert not (context.installation.state_dir / "exports/revoked").exists()
    assert (context.installation.state_dir / "exports/permitted/complete.json").exists()
