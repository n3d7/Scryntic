"""Lazy public exports; metadata imports never load native decoders."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scryntic.publication.coordinator import (
        NoPublishableWork,
        NoRecovery,
        NormalizationReader,
        PublicationCoordinator,
        Published,
        Recovered,
        WaitingForNormalization,
    )
    from scryntic.publication.manifest import (
        GENESIS_MANIFEST_HASH,
        MANIFEST_SCHEMA,
        ManifestBody,
        ManifestDocument,
        ManifestError,
        ManifestRef,
        ManifestStorage,
        parse_manifest,
        prepare_manifest,
    )
    from scryntic.publication.reader import (
        Continuity,
        PublicationReader,
        PublicationReaderError,
        ValidatedManifest,
    )
    from scryntic.publication.sqlite_store import (
        CatalogEntry,
        PendingPublication,
        PendingState,
        PublicationError,
        PublicationLimits,
        PublicationReservation,
        PublicationStatus,
        PublicationStore,
        PublisherOwned,
    )

_EXPORTS = {
    name: "scryntic.publication." + module
    for module, names in (
        (
            "coordinator",
            (
                "NoPublishableWork",
                "NoRecovery",
                "NormalizationReader",
                "PublicationCoordinator",
                "Published",
                "Recovered",
                "WaitingForNormalization",
            ),
        ),
        (
            "manifest",
            (
                "GENESIS_MANIFEST_HASH",
                "MANIFEST_SCHEMA",
                "ManifestBody",
                "ManifestDocument",
                "ManifestError",
                "ManifestRef",
                "ManifestStorage",
                "parse_manifest",
                "prepare_manifest",
            ),
        ),
        (
            "reader",
            (
                "Continuity",
                "PublicationReader",
                "PublicationReaderError",
                "ValidatedManifest",
            ),
        ),
        (
            "sqlite_store",
            (
                "CatalogEntry",
                "PendingPublication",
                "PendingState",
                "PublicationError",
                "PublicationLimits",
                "PublicationReservation",
                "PublicationStatus",
                "PublicationStore",
                "PublisherOwned",
            ),
        ),
    )
    for name in names
}

__all__ = [
    "GENESIS_MANIFEST_HASH",
    "MANIFEST_SCHEMA",
    "ManifestBody",
    "ManifestDocument",
    "ManifestError",
    "ManifestRef",
    "ManifestStorage",
    "CatalogEntry",
    "PendingPublication",
    "PendingState",
    "PublicationError",
    "PublicationLimits",
    "PublicationReservation",
    "PublicationStatus",
    "PublicationStore",
    "PublisherOwned",
    "NoPublishableWork",
    "NoRecovery",
    "NormalizationReader",
    "PublicationCoordinator",
    "Published",
    "Recovered",
    "WaitingForNormalization",
    "Continuity",
    "PublicationReader",
    "PublicationReaderError",
    "ValidatedManifest",
    "parse_manifest",
    "prepare_manifest",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
