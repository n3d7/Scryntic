"""Explicit schema negotiation; no native codec import in the coordinator."""

from typing import Any, cast

from scryntic.domain.identity import SchemaRef, Version

_CANDLE_RECIPE_NAME = "scryntic.dataset.candle-recipe"
CANDLE_RECIPE_SCHEMA = SchemaRef(_CANDLE_RECIPE_NAME, Version(1, 0))
AS_OBSERVED_CANDLE_RECIPE_SCHEMA = SchemaRef(_CANDLE_RECIPE_NAME, Version(1, 1))
F18_RECIPE_SCHEMA = SchemaRef(_CANDLE_RECIPE_NAME, Version(1, 2))
DATASET_SCHEMA = SchemaRef("scryntic.dataset.candle.parquet", Version(1, 0))
EVOLVED_DATASET_SCHEMA = SchemaRef("scryntic.dataset.candle.parquet", Version(1, 1))
MANIFEST_SCHEMA = SchemaRef("scryntic.dataset.manifest", Version(1, 0))
EVOLVED_MANIFEST_SCHEMA = SchemaRef("scryntic.dataset.manifest", Version(1, 1))


def schema_projection(value: SchemaRef) -> dict[str, object]:
    return {
        "name": value.name,
        "major": value.version.major,
        "minor": value.version.minor,
    }


def schema_from(value: object) -> SchemaRef:
    if type(value) is not dict or set(value) != {"name", "major", "minor"}:
        raise ValueError("Invalid schema reference")
    fields = cast(dict[str, Any], value)
    return SchemaRef(fields["name"], Version(fields["major"], fields["minor"]))
