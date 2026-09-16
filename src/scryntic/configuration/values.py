"""Validated ordinary settings and references, never resolved secret material."""

import re
from dataclasses import dataclass, field
from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_CONFIG = "invalid_configuration"
    UNKNOWN_KEY = "unknown_configuration_key"
    UNSAFE_PATH = "unsafe_or_unavailable_path"
    INVALID_CREDENTIAL = "invalid_or_unavailable_credential"
    CAPABILITY_DISABLED = "capability_not_enabled"
    CREDENTIAL_SCOPE = "credential_scope_mismatch"


class BoundaryError(ValueError):
    """Only fixed codes cross the diagnostics boundary; never echo input/errors."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class Profile(StrEnum):
    COLLECTOR = "collector"
    WORKSTATION = "workstation"


class Capability(StrEnum):
    PUBLIC_COLLECTION = "public_collection"
    LOCAL_ANALYSIS = "local_analysis"
    SYNCHRONIZATION = "synchronization"
    TELEGRAM_REPORTS = "telegram_reports"


CREDENTIAL_CAPABILITIES = frozenset(
    {Capability.SYNCHRONIZATION, Capability.TELEGRAM_REPORTS}
)
LOG_LEVELS = frozenset({"debug", "info", "warning", "error"})


def reference_name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value
    ):
        raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)


@dataclass(frozen=True, slots=True)
class SecretReference:
    capability: Capability
    backend: str
    name: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.capability not in CREDENTIAL_CAPABILITIES or self.backend != "file":
            raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)
        reference_name(self.name)


@dataclass(frozen=True, slots=True)
class Configuration:
    profile: Profile
    log_level: str
    enabled: frozenset[Capability]
    references: tuple[SecretReference, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, Profile) or self.log_level not in LOG_LEVELS:
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        if type(self.enabled) is not frozenset or any(
            type(c) is not Capability for c in self.enabled
        ):
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        if type(self.references) is not tuple or any(
            type(r) is not SecretReference for r in self.references
        ):
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        allowed = (
            {Capability.PUBLIC_COLLECTION, Capability.TELEGRAM_REPORTS}
            if self.profile is Profile.COLLECTOR
            else {Capability.LOCAL_ANALYSIS, Capability.SYNCHRONIZATION}
        )
        if not self.enabled <= allowed:
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        if len({r.capability for r in self.references}) != len(self.references):
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        for capability in self.enabled & CREDENTIAL_CAPABILITIES:
            self.required_reference(capability)

    def required_reference(self, capability: Capability) -> SecretReference | None:
        if capability not in self.enabled:
            raise BoundaryError(ErrorCode.CAPABILITY_DISABLED)
        if capability not in CREDENTIAL_CAPABILITIES:
            return None
        for reference in self.references:
            if reference.capability is capability:
                return reference
        raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)

    def diagnostics(self) -> dict[str, str | list[str]]:
        return {
            "profile": self.profile.value,
            "log_level": self.log_level,
            "enabled": sorted(c.value for c in self.enabled),
        }
