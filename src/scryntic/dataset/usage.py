"""Versioned operator source-use decisions, independent of exchange adapters.

This records an operator decision and its evidence hash, not a legal opinion or
an automatic licence verifier. Unknown and unlisted permissions fail closed.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from scryntic.archive.canonical import JsonObject, canonical_json_bytes
from scryntic.domain.validation import digest, identifier, immutable_tuple

_SCHEMA: JsonObject = {"name": "scryntic.source-use-policy", "major": 1, "minor": 0}
_SCOPE = "private-noncommercial-research"


class SourceUseDenied(ValueError):
    """The selected source is not permitted for stored ML feature preparation."""


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("Invalid source-use policy shape")
    return value


@dataclass(frozen=True, slots=True)
class _Grant:
    source_id: str
    status: str
    allow_storage: bool
    allow_feature_derivation: bool
    evidence_sha256: str | None

    def __post_init__(self) -> None:
        identifier(self.source_id)
        if type(self.status) is not str or self.status not in (
            "pending",
            "permitted",
            "denied",
        ):
            raise ValueError("Unsupported source-use status")
        if (
            type(self.allow_storage) is not bool
            or type(self.allow_feature_derivation) is not bool
        ):
            raise TypeError("Source permissions must be explicit booleans")
        if self.evidence_sha256 is not None:
            digest(self.evidence_sha256)
        elif self.status == "permitted":
            raise ValueError("Permitted source needs an evidence digest")


@dataclass(frozen=True, slots=True)
class SourceUsePolicy:
    policy_id: str
    grants: tuple[_Grant, ...]

    def __post_init__(self) -> None:
        identifier(self.policy_id)
        immutable_tuple(self.grants, 64)
        if not self.grants or any(type(grant) is not _Grant for grant in self.grants):
            raise ValueError("Expected bounded source grants")
        if len({grant.source_id for grant in self.grants}) != len(self.grants):
            raise ValueError("Duplicate source grant")

    @classmethod
    def from_document(cls, value: object) -> "SourceUsePolicy":
        document = _object(value, {"schema", "policy_id", "scope", "sources"})
        schema = _object(document["schema"], {"name", "major", "minor"})
        if (
            type(schema["major"]) is not int
            or type(schema["minor"]) is not int
            or schema != _SCHEMA
        ):
            raise ValueError("Unsupported source-use policy schema")
        if document["scope"] != _SCOPE:
            raise ValueError("Unsupported source-use scope")
        sources = document["sources"]
        if type(sources) is not list or not 1 <= len(sources) <= 64:
            raise ValueError("Expected bounded source grants")
        keys = {
            "source_id",
            "status",
            "allow_storage",
            "allow_feature_derivation",
            "evidence_sha256",
        }
        grants = tuple(_Grant(**_object(source, keys)) for source in sources)
        return cls(document["policy_id"], grants)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "schema": dict(_SCHEMA),
                "policy_id": self.policy_id,
                "scope": _SCOPE,
                "sources": [
                    asdict(grant)
                    for grant in sorted(self.grants, key=lambda g: g.source_id)
                ],
            }
        )

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()

    def authorize(self, sources: tuple[str, ...]) -> None:
        immutable_tuple(sources, 4096)
        if not sources:
            raise SourceUseDenied("No sources selected for ML preparation")
        grants = {grant.source_id: grant for grant in self.grants}
        for source in sources:
            identifier(source)
            grant = grants.get(source)
            if (
                grant is None
                or grant.status != "permitted"
                or not grant.allow_storage
                or not grant.allow_feature_derivation
            ):
                raise SourceUseDenied(
                    "Source policy denies stored ML feature preparation"
                )
