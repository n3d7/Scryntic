"""Exact operator-owned model reviews; compatibility never implies approval."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date
from hashlib import sha256
from typing import Any

from scryntic.application.providers import ForecastRequest, ProviderDescriptor
from scryntic.archive.canonical import canonical_json_bytes
from scryntic.domain.validation import digest, identifier, immutable_tuple

INPUT_FIELDS = frozenset({"start_ns", "interval_ns", "close"})
_UNKNOWN = frozenset(
    {"unknown", "unspecified", "none", "latest", "main", "master", "head"}
)


def projection(value: Any) -> Any:
    """Convert owned immutable contracts to bounded JSON-compatible primitives."""
    if is_dataclass(value) and not isinstance(value, type):
        return projection(asdict(value))
    if type(value) is dict:
        return {key: projection(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [projection(item) for item in value]
    if type(value) is frozenset:
        return sorted(value)
    if type(value) is float:
        return repr(value)
    return value


def fingerprint(value: Any) -> str:
    return sha256(canonical_json_bytes(projection(value))).hexdigest()


def _known(value: object) -> None:
    if type(value) is not str or value.casefold() in _UNKNOWN:
        raise ValueError("Missing or unknown model admission metadata")
    identifier(value)


@dataclass(frozen=True, slots=True)
class LoadingRequirements:
    runtime: str = "cpython-3.12"
    format: str = "primitive-json"
    code: str = "builtin"
    network: bool = False
    downloads: bool = False
    device: str = "cpu"
    max_memory_bytes: int = 32 * 1024 * 1024

    def require_safe(self) -> None:
        if (
            self.runtime != "cpython-3.12"
            or self.format != "primitive-json"
            or self.code != "builtin"
            or type(self.network) is not bool
            or self.network
            or type(self.downloads) is not bool
            or self.downloads
            or self.device != "cpu"
            or type(self.max_memory_bytes) is not int
            or not 1 <= self.max_memory_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("Unsafe or unknown model loading requirements")


@dataclass(frozen=True, slots=True, kw_only=True)
class AdmissionPolicy:
    policy_id: str
    approved_reviews: tuple[str, ...]
    approved_licenses: frozenset[str]
    allowed_uses: frozenset[str]
    remote_enabled: bool = False
    allowed_input_fields: frozenset[str] = INPUT_FIELDS

    def __post_init__(self) -> None:
        identifier(self.policy_id)
        immutable_tuple(self.approved_reviews, 64)
        for value in self.approved_reviews:
            digest(value)
        for values in (
            self.approved_licenses,
            self.allowed_uses,
            self.allowed_input_fields,
        ):
            if type(values) is not frozenset or len(values) > 64:
                raise ValueError("Expected bounded immutable admission grants")
            for value in values:
                identifier(value)
        if type(self.remote_enabled) is not bool:
            raise ValueError("Remote authorization must be explicit")

    @property
    def sha256(self) -> str:
        return fingerprint(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelReview:
    descriptor: ProviderDescriptor
    package_license: str
    code_license: str
    weight_license: str
    model_card: str
    terms: str
    reviewed_on: str
    verified_artifacts: tuple[str, ...]
    loading: LoadingRequirements

    @property
    def sha256(self) -> str:
        return fingerprint(self)

    def _metadata(self) -> None:
        descriptor = self.descriptor
        model = descriptor.model
        if (
            type(model.origin) is not str
            or not model.origin.strip()
            or model.origin.strip().casefold() in _UNKNOWN
            or not model.origin.isprintable()
            or len(model.origin) > 1024
        ):
            raise ValueError("Missing or unknown model origin")
        for value in (
            model.publisher,
            model.revision,
            model.license_id,
            model.usage_policy_id,
            self.package_license,
            self.code_license,
            self.weight_license,
            self.model_card,
            self.terms,
        ):
            _known(value)
        if type(self.reviewed_on) is not str or len(self.reviewed_on) != 10:
            raise ValueError("Missing review date")
        if date.fromisoformat(self.reviewed_on).isoformat() != self.reviewed_on:
            raise ValueError("Invalid review date")
        immutable_tuple(self.verified_artifacts, 64)
        for value in self.verified_artifacts:
            digest(value)
        if (
            not self.verified_artifacts
            or model.artifact_sha256 != self.verified_artifacts
        ):
            raise ValueError("Unverified model artifacts")
        if model.loading_requirements != ("builtin-json-only",):
            raise ValueError("Unsafe or unknown loading declaration")
        self.loading.require_safe()

    def admit(
        self, policy: AdmissionPolicy, request: ForecastRequest, intended_use: str
    ) -> None:
        self._metadata()
        self.descriptor.require(request)
        if (
            self.sha256 not in policy.approved_reviews
            or self.descriptor.model.usage_policy_id != policy.policy_id
            or intended_use not in policy.allowed_uses
            or policy.allowed_input_fields != INPUT_FIELDS
            or self.descriptor.max_rows > 1024
            or self.descriptor.max_horizon > 24
        ):
            raise ValueError("Provider/model/use not explicitly approved")
        licenses = (
            self.descriptor.model.license_id,
            self.package_license,
            self.code_license,
            self.weight_license,
        )
        if any(value not in policy.approved_licenses for value in licenses):
            raise ValueError("Model/package/code/weight license not approved")
        if self.descriptor.execution == "remote" and not (
            policy.remote_enabled and request.allow_remote
        ):
            raise ValueError("Remote execution is disabled")
