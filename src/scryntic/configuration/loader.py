"""One TOML document plus allowlisted overrides; no environment settings or startup."""

import tomllib
from collections.abc import Mapping

from scryntic.configuration.paths import Installation, read_file, validate_directories
from scryntic.configuration.values import (
    LOG_LEVELS,
    BoundaryError,
    Capability,
    Configuration,
    ErrorCode,
    Profile,
    SecretReference,
)

_MAX_CONFIG_BYTES = 65536


def _table(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BoundaryError(ErrorCode.INVALID_CONFIG)
    if any(not isinstance(key, str) or key not in keys for key in value):
        raise BoundaryError(ErrorCode.UNKNOWN_KEY)
    return dict(value)


def _configuration(
    data: dict[str, object], overrides: Mapping[str, object]
) -> Configuration:
    settings = _table(data, {"profile", "log_level", "capabilities", "credentials"})
    override = _table(dict(overrides), {"profile", "log_level"})
    # Validate each layer before applying precedence, so overrides cannot mask errors.
    for layer in (settings, override):
        if "profile" in layer and layer["profile"] not in ("collector", "workstation"):
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        if "log_level" in layer and (
            not isinstance(layer["log_level"], str)
            or layer["log_level"] not in LOG_LEVELS
        ):
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
    profile_value = override.get("profile", settings.get("profile", "workstation"))
    profile = Profile(str(profile_value))
    log_level = str(override.get("log_level", settings.get("log_level", "info")))
    default = (
        Capability.PUBLIC_COLLECTION
        if profile is Profile.COLLECTOR
        else Capability.LOCAL_ANALYSIS
    )
    enabled = {default}
    capabilities = _table(
        settings.get("capabilities", {}), {c.value for c in Capability}
    )
    for name, value in capabilities.items():
        if type(value) is not bool:
            raise BoundaryError(ErrorCode.INVALID_CONFIG)
        capability = Capability(name)
        if value:
            enabled.add(capability)
        else:
            enabled.discard(capability)
    credentials = _table(settings.get("credentials", {}), {"telegram", "transfer"})
    references = []
    for name, value in credentials.items():
        spec = _table(value, {"backend", "name"})
        if (
            set(spec) != {"backend", "name"}
            or type(spec["backend"]) is not str
            or type(spec["name"]) is not str
        ):
            raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)
        capability = (
            Capability.TELEGRAM_REPORTS
            if name == "telegram"
            else Capability.SYNCHRONIZATION
        )
        references.append(SecretReference(capability, spec["backend"], spec["name"]))
    return Configuration(
        profile,
        log_level,
        frozenset(enabled),
        tuple(sorted(references, key=lambda r: r.capability)),
    )


def _parse(data: bytes, overrides: Mapping[str, object]) -> Configuration | ErrorCode:
    try:
        return _configuration(tomllib.loads(data.decode("utf-8")), overrides)
    except BoundaryError as error:
        return error.code
    except (ValueError, TypeError, RecursionError):
        return ErrorCode.INVALID_CONFIG


def load_configuration(
    installation: Installation, *, overrides: Mapping[str, object] | None = None
) -> Configuration:
    validate_directories(installation)
    data = read_file(
        installation.config_dir,
        "config.toml",
        installation.config_uid,
        private=not installation.service_mode,
        secret=False,
        limit=_MAX_CONFIG_BYTES,
    )
    result = _parse(data, {} if overrides is None else overrides)
    del data
    if isinstance(result, ErrorCode):
        raise BoundaryError(result)
    return result
