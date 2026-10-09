"""Stable operator configuration selects only application-owned definitions."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from scryntic.domain.validation import digest
from scryntic.models.definitions import DEFINITIONS


def supported_models() -> tuple[str, ...]:
    return tuple(item.name for item in DEFINITIONS)


@dataclass(frozen=True, slots=True)
class ModelSelection:
    selected: str
    artifacts: Path | None = None
    runtime: Path | None = None
    runtime_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.selected not in supported_models():
            raise ValueError("Unknown registered model")
        real = (
            next(item for item in DEFINITIONS if item.name == self.selected).adapter
            is not None
        )
        if real != (
            self.artifacts is not None
            and self.runtime is not None
            and self.runtime_sha256 is not None
        ) or (
            not real
            and (
                self.artifacts is not None
                or self.runtime is not None
                or self.runtime_sha256 is not None
            )
        ):
            raise ValueError("Selected model requires its fixed provisioning inputs")
        for path in (self.artifacts, self.runtime):
            if path is not None and (not path.is_absolute() or ".." in path.parts):
                raise ValueError("Provisioning paths must be absolute")
        if self.runtime_sha256 is not None:
            digest(self.runtime_sha256)

    @classmethod
    def read(cls, path: Path) -> "ModelSelection":
        if path.stat().st_size > 4096:
            raise ValueError("Model configuration exceeds bounds")
        value = tomllib.loads(path.read_text())
        if set(value) != {"model"} or type(value["model"]) is not dict:
            raise ValueError("Expected one model configuration")
        model = value["model"]
        if not {"selected"} <= set(model) <= {
            "selected",
            "artifacts",
            "runtime",
            "runtime_sha256",
        } or any(type(v) is not str for v in model.values()):
            raise ValueError("Unknown model configuration fields")
        return cls(
            model["selected"],
            artifacts=Path(model["artifacts"]) if "artifacts" in model else None,
            runtime=Path(model["runtime"]) if "runtime" in model else None,
            runtime_sha256=model.get("runtime_sha256"),
        )
