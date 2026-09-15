"""Namespaced identity and explicitly negotiated schema versions."""

from dataclasses import dataclass

from scryntic.domain.validation import identifier, integer


@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int

    def __post_init__(self) -> None:
        integer(self.major, 1)
        integer(self.minor, 0)


CONTRACT_VERSION = Version(1, 0)


@dataclass(frozen=True, slots=True)
class SchemaRef:
    name: str
    version: Version

    def __post_init__(self) -> None:
        identifier(self.name)

    def require_readable(self, incoming: "SchemaRef") -> None:
        """A reader opts into a name, major and highest understood minor."""
        if (
            self.name != incoming.name
            or self.version.major != incoming.version.major
            or incoming.version.minor > self.version.minor
        ):
            raise ValueError("Unsupported schema")


@dataclass(frozen=True, slots=True)
class InstrumentId:
    venue: str
    category: str
    symbol: str

    def __post_init__(self) -> None:
        for value in (self.venue, self.category, self.symbol):
            identifier(value)


@dataclass(frozen=True, slots=True)
class EntityId:
    kind: str
    namespace: str
    value: str

    def __post_init__(self) -> None:
        for value in (self.kind, self.namespace, self.value):
            identifier(value)


type SubjectId = InstrumentId | EntityId
