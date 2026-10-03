"""Actual restricted imported recipes preserve observation and derivation lineage."""

import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from scryntic.application.dto import BuildDatasetRequest
from scryntic.archive.canonical import canonical_json_bytes, input_fingerprint
from scryntic.archive.normalized_parquet import NormalizedParquetArchive
from scryntic.archive.raw_parquet import ParquetRawArchive
from scryntic.configuration.paths import Installation
from scryntic.dataset.recipes import CoverageClaim, RecipePolicy
from scryntic.dataset.selection import SourceInput
from scryntic.dataset.service import F18_RECIPE_SCHEMA, DatasetService
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.imports.catalog import ImportCatalog
from scryntic.imports.protocol import ImportLimits, parse_request
from scryntic.normalization.sqlite_store import OutcomeKind
from scryntic.publication.coordinator import PublicationCoordinator, Published
from scryntic.publication.manifest import ManifestStorage
from tests.dataset.test_recipes import _received
from tests.dataset.test_selection import _source
from tests.normalization.helpers import DEFAULT_RECEIPT, FixedRawReader, installation
from tests.publication.helpers import (
    FixedNormalizationReader,
    configured_coordinator,
    limits,
)


def _clock(wall: int, uncertainty: int = 0) -> ClockSample:
    return ClockSample(
        wall, 0, "session", TimeQuality("epoch", "healthy", 0, uncertainty, 0)
    )


def _publish(tmp_path: Path, sources: tuple[SourceInput, ...]) -> tuple[bytes, Path]:
    bundle = configured_coordinator(
        tmp_path / "collector",
        offsets=tuple(range(1, len(sources) + 1)),
        epochs=("epoch-a",) * len(sources),
    )
    try:
        records = tuple(source.value.raw for source in sources)
        raw_reader = FixedRawReader(records)
        normalized = FixedNormalizationReader(records)
        for index, source in enumerate(sources):
            value = source.value
            normalized.outcomes[value.raw.identity] = replace(
                value.outcome,
                predecessor=None if index == 0 else records[index - 1].identity,
            )
            assert value.semantics is not None
            assert value.outcome.semantic_revision is not None
            normalized.semantics[value.outcome.semantic_revision] = value.semantics
        # Configure the public coordinator with actual recipe history, not synthetic decoded rows.
        bundle.coordinator = PublicationCoordinator(
            raw_reader,
            normalized,
            bundle.store,
            ParquetRawArchive(bundle.root),
            NormalizedParquetArchive(bundle.root),
            ManifestStorage(bundle.root),
            bundle.root,
            limits(),
        )
        published = bundle.coordinator.publish_next()
        assert isinstance(published, Published)
        data = ManifestStorage(bundle.root).read_exact(published.manifest, 65536)
        document = parse_request(data, ImportLimits())
        incoming = tmp_path / "incoming"
        incoming.mkdir(mode=0o700)
        for descriptor in document.body.objects:
            path = (
                bundle.root.state_dir
                / "archive/objects/sha256"
                / descriptor.sha256[:2]
                / f"{descriptor.sha256}.parquet"
            )
            shutil.copyfile(path, incoming / descriptor.sha256)
            (incoming / descriptor.sha256).chmod(0o600)
        return data, incoming
    finally:
        bundle.close()


def _service(catalog: ImportCatalog, root: Installation) -> DatasetService:
    return DatasetService(
        catalog, root, code_revision="fixture", dependency_lock_sha256="d" * 64
    )


def test_imported_revisions_first_latest_and_uncertain_cutoff(tmp_path: Path) -> None:
    first = _received(_source(1), 10)
    revision = _received(_source(2, close="100.20", kind=OutcomeKind.OPEN_REVISION), 20)
    final = _received(
        _source(3, close="100.30", finalized=True, kind=OutcomeKind.FINALIZATION), 30
    )
    data, incoming = _publish(tmp_path, (first, revision, final))
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = _service(catalog, root)
        request = BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA)
        latest = service.build(request).dataset
        earliest = service.build(request, policy=RecipePolicy(revision="first")).dataset
        assert service.inspect(latest)[0]["close"] == "100.300000000000000000"
        assert service.inspect(earliest)[0]["close"] == "100.750000000000000000"
        manifest = service.read_manifest(earliest)
        assert len(manifest["rows"][0]["evidence"]) == 3
        assert manifest["rows"][0]["selected"][
            "input_fingerprint"
        ] == input_fingerprint(first.value)
        assert imported.manifest_hash in service.pins(earliest)["publication_manifests"]
        strict = service.build(
            replace(request, as_observed_cutoff=_clock(21, uncertainty=2)),
            policy=RecipePolicy(mode="as-observed"),
        ).dataset
        assert service.inspect(strict)[0]["close"] == "100.750000000000000000"
        strict_manifest = service.read_manifest(strict)
        assert len(strict_manifest["rows"][0]["evidence"]) == 1
        assert strict_manifest["coverage"]["validated_input_count"] == 3


def test_imported_lag_label_values_null_gaps_and_fingerprints(tmp_path: Path) -> None:
    first = _source(1, finalized=True)
    semantics = first.value.semantics
    assert semantics is not None
    key = semantics.key
    second = _source(
        2, finalized=True, close="100.20", start_ns=key.start_ns + key.interval_ns
    )
    fourth = _source(3, finalized=True, start_ns=key.start_ns + 3 * key.interval_ns)
    data, incoming = _publish(tmp_path, (first, second, fourth))
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = _service(catalog, root)
        reference = service.build(
            BuildDatasetRequest((imported.manifest_hash,), F18_RECIPE_SCHEMA),
            policy=RecipePolicy(lag_steps=1, label_horizon_steps=1),
        ).dataset
        rows = service.inspect(reference)
        assert rows[0]["label_close"] == "100.200000000000000000"
        assert rows[0]["label_end_ns"] == key.start_ns + 2 * key.interval_ns
        assert rows[1]["lag_close"] == "100.750000000000000000"
        assert rows[1]["label_close"] is None
        assert rows[2]["lag_close"] is None
        manifest = service.read_manifest(reference)
        definitions = manifest["derived_lineage"]
        published_second = replace(
            second.value,
            outcome=replace(second.value.outcome, predecessor=first.value.raw.identity),
        )
        assert definitions[0]["label_source_fingerprints"] == [
            input_fingerprint(published_second)
        ]
        assert definitions[0]["label_source_fingerprints"] == [
            manifest["rows"][1]["selected"]["input_fingerprint"]
        ]
        assert definitions[1]["label_missing_reason"] == "gap-or-missing"


def test_strict_imported_late_coverage_repair_stays_unknown_and_pinned(
    tmp_path: Path,
) -> None:
    source = _received(_source(1), 10)
    semantics = source.value.semantics
    assert semantics is not None
    key = semantics.key
    repair = CoverageClaim(
        key.instrument,
        key.interval_ns,
        key.start_ns,
        key.start_ns + key.interval_ns,
        "complete",
        _clock(30),
    )
    data, incoming = _publish(tmp_path, (source,))
    root = installation(tmp_path / "workstation")
    with ImportCatalog(root) as catalog:
        imported = catalog.accept(data, incoming, DEFAULT_RECEIPT)
        service = _service(catalog, root)
        reference = service.build(
            BuildDatasetRequest(
                (imported.manifest_hash,),
                F18_RECIPE_SCHEMA,
                as_observed_cutoff=_clock(20),
            ),
            policy=RecipePolicy(mode="as-observed", coverage="exclude"),
            coverage=(repair,),
        ).dataset
        assert service.inspect(reference) == []
        manifest = service.read_manifest(reference)
        assert any(
            record.get("coverage_status") == "unknown"
            for record in manifest["exclusions"]
        )
        expected = sha256(canonical_json_bytes([repair.projection()])).hexdigest()
        assert (
            manifest["coverage_sha256"]
            == service.pins(reference)["coverage_sha256"]
            == expected
        )
