"""Explicit feature preparation over the existing accepted-data/F18 boundary."""

from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from scryntic.application.dto import BuildDatasetRequest
from scryntic.archive.canonical import JsonValue, canonical_json_bytes
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy
from scryntic.dataset.service import DatasetService
from scryntic.dataset.usage import SourceUsePolicy
from scryntic.domain.dataset import DatasetRef
from scryntic.imports.catalog import ImportCatalog


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    dataset: DatasetRef
    policy_bytes: bytes
    receipt_bytes: bytes
    manifest_bytes: bytes
    parquet_bytes: bytes

    @property
    def receipt_sha256(self) -> str:
        return sha256(self.receipt_bytes).hexdigest()

    def export_files(self) -> dict[str, bytes]:
        return {
            "dataset.manifest.json": self.manifest_bytes,
            "dataset.parquet": self.parquet_bytes,
            "source-use-policy.json": self.policy_bytes,
            "preparation.json": self.receipt_bytes,
        }


def prepare_dataset(
    catalog: ImportCatalog,
    datasets: DatasetService,
    request: BuildDatasetRequest,
    *,
    usage_policy: SourceUsePolicy,
    policy: RecipePolicy,
    coverage: tuple[CoverageClaim, ...] = (),
) -> PreparedDataset:
    """Check source grants before native processing or dataset publication.

    This does not grant training, holdout access or model admission. The caller
    publishes the complete export bundle through the existing durable exporter.
    """
    if policy.label_horizon_steps:
        raise ValueError(
            "ML preparation cannot derive labels before research safeguards"
        )
    source_ids = tuple(
        sorted(
            {
                catalog.resolve(value).body.partition.source
                for value in request.input_manifests
            }
        )
    )
    usage_policy.authorize(source_ids)
    reference = datasets.build(request, policy=policy, coverage=coverage).dataset
    manifest, parquet = datasets.export(reference)
    receipt = canonical_json_bytes(
        {
            "schema": {"name": "scryntic.ml-preparation", "major": 1, "minor": 0},
            "purpose": "features-only",
            "dataset_manifest_sha256": reference.manifest_sha256,
            "input_manifests": cast(list[JsonValue], sorted(request.input_manifests)),
            "source_ids": list(source_ids),
            "policy_sha256": usage_policy.sha256,
            "recipe_policy": policy.projection(),
        }
    )
    return PreparedDataset(
        reference, usage_policy.canonical_bytes, receipt, manifest, parquet
    )
