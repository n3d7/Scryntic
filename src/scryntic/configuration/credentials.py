"""Adapter-local file credentials; never imported by core contract modules."""

from types import TracebackType
from typing import Never

from scryntic.configuration.paths import Installation, read_file
from scryntic.configuration.values import (
    CREDENTIAL_CAPABILITIES,
    BoundaryError,
    Capability,
    Configuration,
    ErrorCode,
)


class Secret:
    """Short-lived sensitive buffer; Python cannot guarantee elimination of all copies."""

    __slots__ = ("_buffer", "_closed")

    def __init__(self, data: bytes) -> None:
        self._buffer = bytearray(data)
        self._closed = False

    def __repr__(self) -> str:
        return "<Secret redacted>"

    __str__ = __repr__

    def __reduce__(self) -> Never:
        raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)

    def view(self) -> memoryview:
        if self._closed:
            raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)
        return memoryview(self._buffer).toreadonly()

    def close(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._closed = True

    def __enter__(self) -> "Secret":
        if self._closed:
            raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class FileCredentials:
    """Construct only inside the enabled owning adapter, with one fixed capability."""

    __slots__ = ("_installation", "_reference")

    def __init__(
        self, installation: Installation, config: Configuration, capability: Capability
    ) -> None:
        if capability not in CREDENTIAL_CAPABILITIES:
            raise BoundaryError(ErrorCode.CREDENTIAL_SCOPE)
        reference = config.required_reference(capability)
        if reference is None:
            raise BoundaryError(ErrorCode.CREDENTIAL_SCOPE)
        self._installation = installation
        self._reference = reference

    def __repr__(self) -> str:
        return "<FileCredentials scoped>"

    def open(self) -> Secret:
        data = read_file(
            self._installation.credential_dir,
            self._reference.name,
            self._installation.owner_uid,
            private=True,
            secret=True,
            limit=65536,
        )
        if not data:
            raise BoundaryError(ErrorCode.INVALID_CREDENTIAL)
        return Secret(data)
