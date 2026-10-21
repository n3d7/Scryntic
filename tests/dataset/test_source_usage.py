"""Source permissions are explicit, versioned and independent of venue adapters."""

from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest


def policy_document() -> dict[str, Any]:
    return {
        "schema": {"name": "scryntic.source-use-policy", "major": 1, "minor": 0},
        "policy_id": "private-research-v1",
        "scope": "private-noncommercial-research",
        "sources": [
            {
                "source_id": source,
                "status": "permitted",
                "allow_storage": True,
                "allow_feature_derivation": True,
                "evidence_sha256": "a" * 64,
            }
            for source in ("bybit-public", "binance-vision")
        ],
    }


def test_policy_is_canonical_and_source_independent() -> None:
    from scryntic.dataset.usage import SourceUsePolicy

    document = policy_document()
    policy = SourceUsePolicy.from_document(document)
    policy.authorize(("bybit-public", "binance-vision"))
    assert policy.sha256 == sha256(policy.canonical_bytes).hexdigest()
    document["sources"].reverse()
    assert (
        SourceUsePolicy.from_document(document).canonical_bytes
        == policy.canonical_bytes
    )
    document["sources"][0]["allow_feature_derivation"] = False
    assert SourceUsePolicy.from_document(document).sha256 != policy.sha256


@pytest.mark.parametrize("status", ["pending", "denied"])
def test_unresolved_sources_are_not_silently_permitted(status: str) -> None:
    from scryntic.dataset.usage import SourceUseDenied, SourceUsePolicy

    document = policy_document()
    document["sources"][0]["status"] = status
    policy = SourceUsePolicy.from_document(document)
    with pytest.raises(SourceUseDenied):
        policy.authorize(("bybit-public",))


def test_all_sources_need_storage_and_derivation_permission() -> None:
    from scryntic.dataset.usage import SourceUseDenied, SourceUsePolicy

    policy = SourceUsePolicy.from_document(policy_document())
    with pytest.raises(SourceUseDenied):
        policy.authorize(("bybit-public", "unregistered-source"))
    for field in ("allow_storage", "allow_feature_derivation"):
        document = policy_document()
        document["sources"][0][field] = False
        restricted = SourceUsePolicy.from_document(document)
        with pytest.raises(SourceUseDenied):
            restricted.authorize(("bybit-public",))


@pytest.mark.parametrize(
    "change",
    [
        {"schema": {"name": "scryntic.source-use-policy", "major": 2, "minor": 0}},
        {"schema": {"name": "scryntic.source-use-policy", "major": True, "minor": 0}},
        {"scope": "live-trading"},
        {"extra": True},
        {"sources": []},
    ],
)
def test_policy_rejects_unsupported_schema_scope_and_shape(
    change: dict[str, Any],
) -> None:
    from scryntic.dataset.usage import SourceUsePolicy

    document = policy_document() | change
    with pytest.raises((ValueError, TypeError)):
        SourceUsePolicy.from_document(document)


def test_policy_rejects_duplicate_sources_coercion_and_missing_evidence() -> None:
    from scryntic.dataset.usage import SourceUsePolicy

    document = policy_document()
    duplicate = deepcopy(document)
    duplicate["sources"].append(duplicate["sources"][0])
    coerced = deepcopy(document)
    coerced["sources"][0]["allow_storage"] = "true"
    missing = deepcopy(document)
    missing["sources"][0]["evidence_sha256"] = None
    for invalid in (duplicate, coerced, missing):
        with pytest.raises((ValueError, TypeError)):
            SourceUsePolicy.from_document(invalid)
