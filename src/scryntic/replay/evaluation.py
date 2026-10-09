"""Frozen training transforms, naive close forecasts and deterministic metrics."""

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

from scryntic.replay.contracts import Partition, ReplayClock, ReplayFeatures, Series
from scryntic.replay.reader import CandleReplayReader, ReplayTarget


def arithmetic_context() -> Context:
    return Context(
        prec=80,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def series_projection(series: Series) -> dict[str, Any]:
    return dict(
        zip(("venue", "category", "symbol", "interval_ns"), series, strict=True)
    )


@dataclass(frozen=True, slots=True)
class ForecastCase:
    features: ReplayFeatures
    partition: Partition
    target: ReplayTarget
    prediction: Decimal
    score_cutoff_ns: int


@dataclass(frozen=True, slots=True)
class Standardizer:
    mean: tuple[Decimal, ...]
    scale: tuple[Decimal, ...]
    fit_row_indices: tuple[int, ...]

    def transform(self, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        with localcontext(arithmetic_context()):
            return tuple(
                (value - mean) / scale
                for value, mean, scale in zip(
                    values, self.mean, self.scale, strict=True
                )
            )

    def projection(self) -> dict[str, Any]:
        return {
            "version": "train-population-standardizer-v1",
            "mean": [decimal_text(value) for value in self.mean],
            "scale": [decimal_text(value) for value in self.scale],
            "fit_row_indices": list(self.fit_row_indices),
            "constant_feature_scale": "1",
        }


def fit_standardizer(cases: tuple[ForecastCase, ...]) -> Standardizer:
    if not cases or any(case.partition != "train" for case in cases):
        raise ValueError("Transforms require nonempty training cases only")
    if len({case.features.series for case in cases}) != 1:
        raise ValueError("Transforms require a single candle series")
    with localcontext(arithmetic_context()):
        columns = tuple(zip(*(case.features.values for case in cases), strict=True))
        means = tuple(sum(column, Decimal(0)) / len(column) for column in columns)
        scales = tuple(
            (
                sum(((value - mean) ** 2 for value in column), Decimal(0)) / len(column)
            ).sqrt()
            or Decimal(1)
            for mean, column in zip(means, columns, strict=True)
        )
    return Standardizer(means, scales, tuple(case.features.row_index for case in cases))


def collect_cases(
    reader: CandleReplayReader,
) -> tuple[list[ForecastCase], list[dict[str, Any]]]:
    cases: list[ForecastCase] = []
    exclusions: list[dict[str, Any]] = []
    config = reader.config
    ordered = sorted(
        range(len(reader.rows)),
        key=lambda i: (
            reader.rows[i]["start_ns"] + reader.rows[i]["interval_ns"],
            reader.series(i),
        ),
    )
    clock = ReplayClock(config.start_ns, uncertainty_ns=config.decision_uncertainty_ns)
    for i in ordered:
        row = reader.rows[i]
        origin = row["start_ns"] + row["interval_ns"] + config.decision_delay_ns
        radius = config.decision_uncertainty_ns
        window = config.partition(origin - radius, origin + radius)
        if window is None:
            exclusions.append({"row_index": i, "reason": "outside-or-crossing-split"})
            continue
        name, boundary = window
        frame, reasons = reader.feature_result(i, clock.advance(origin))
        if frame is None:
            exclusions.extend(
                {"row_index": i, "partition": name, "reason": reason}
                for reason in reasons
            )
            continue
        case, reasons = _forecast_case(reader, frame, name, boundary, origin + radius)
        if case is None:
            exclusions.extend(
                {"row_index": i, "partition": name, "reason": reason}
                for reason in reasons
            )
        else:
            cases.append(case)
    return cases, exclusions


def _forecast_case(
    reader: CandleReplayReader,
    frame: ReplayFeatures,
    partition: Partition,
    boundary: int,
    latest_decision: int,
) -> tuple[ForecastCase | None, tuple[str, ...]]:
    # Predict before requesting a label. No future target reaches the baseline.
    prediction = frame.values[0]
    label_end = reader.rows[frame.row_index]["label_end_ns"]
    if label_end is not None and label_end <= latest_decision:
        return None, ("label-not-future",)
    if label_end is not None and label_end >= boundary:
        return None, ("label-purged",)
    target, reasons = reader.target_result(
        frame.row_index, ReplayClock(boundary - 1).sample
    )
    if target is None:
        return None, tuple(
            "label-availability-purged" if reason == "label-unavailable" else reason
            for reason in reasons
        )
    if not reader.config.label_fits(target.end_ns, target.available_ns, boundary):
        raise ValueError("Label crossed partition cutoff")
    return ForecastCase(frame, partition, target, prediction, boundary - 1), ()


def score_cases(cases: list[ForecastCase]) -> dict[str, Any]:
    if not cases:
        raise ValueError("Metrics require nonempty comparable cases")
    with localcontext(arithmetic_context()):
        errors = [case.prediction - case.target.value for case in cases]
        count = len(errors)
        return {
            "count": count,
            "mae": decimal_text(
                sum((abs(error) for error in errors), Decimal(0)) / count
            ),
            "rmse": decimal_text(
                (sum((error * error for error in errors), Decimal(0)) / count).sqrt()
            ),
            "bias": decimal_text(sum(errors, Decimal(0)) / count),
        }


def evaluate(reader: CandleReplayReader) -> dict[str, Any]:
    cases, exclusions = collect_cases(reader)
    transforms: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for series in sorted({case.features.series for case in cases}):
        selected = [case for case in cases if case.features.series == series]
        training = tuple(case for case in selected if case.partition == "train")
        if not training:
            exclusions.extend(
                {
                    "row_index": case.features.row_index,
                    "partition": case.partition,
                    "reason": "no-training-data",
                }
                for case in selected
            )
            continue
        transform = fit_standardizer(training)
        transforms.append(
            {"series": series_projection(series), **transform.projection()}
        )
        for partition in ("train", "validation", "test"):
            partition_cases = [case for case in selected if case.partition == partition]
            if partition_cases:
                metrics.append(
                    {
                        "series": series_projection(series),
                        "partition": partition,
                        **score_cases(partition_cases),
                    }
                )
        for case in selected:
            predictions.append(
                {
                    "row_index": case.features.row_index,
                    "series": series_projection(series),
                    "partition": case.partition,
                    "decision_ns": case.features.decision_ns,
                    "score_cutoff_ns": case.score_cutoff_ns,
                    "features": [decimal_text(value) for value in case.features.values],
                    "transformed_features": [
                        decimal_text(value)
                        for value in transform.transform(case.features.values)
                    ],
                    "prediction": decimal_text(case.prediction),
                    "actual": decimal_text(case.target.value),
                    "label_end_ns": case.target.end_ns,
                    "label_available_ns": case.target.available_ns,
                    "quality_flags": sorted(
                        set(case.features.flags) | set(case.target.flags)
                    ),
                }
            )
    return {
        "schema": {"name": "scryntic-baseline-evaluation", "major": 1, "minor": 0},
        "status": "evaluated" if transforms else "no-training-data",
        "configuration": reader.config.projection(),
        "baseline": {
            "version": "last-available-close-v1",
            "parameters": {},
            "randomness": "none",
        },
        "arithmetic": {
            "type": "decimal",
            "precision": 80,
            "rounding": "ROUND_HALF_EVEN",
        },
        "feature_names": ["close"] + (["lag_close"] if reader.policy.lag_steps else []),
        "counts": {
            "candidates": len(reader.rows),
            "scored": len(predictions),
            "excluded": len({item["row_index"] for item in exclusions}),
        },
        "transforms": transforms,
        "metrics": metrics,
        "predictions": sorted(predictions, key=lambda item: item["row_index"]),
        "exclusions": sorted(
            exclusions, key=lambda item: (item["row_index"], item["reason"])
        ),
        "dataset_exclusions": reader.manifest["exclusions"],
        "derived_lineage": reader.manifest["derived_lineage"],
        "limitations": [
            "Synthetic decisions do not prove physical or workstation possession time.",
            "As-observed uses collector receipt bounds; unavailable selected revisions are excluded, not reconstructed.",
            "Coverage requires explicit captured claims; received rows never imply completeness.",
            "No imputation, parameter search, trading simulation or financial execution.",
            "Metrics use price units per series; training metrics are descriptive, not held-out accuracy.",
            "Reproducibility requires retained pinned inputs and the recorded code/runtime/lock; no forecasting advantage is promised.",
        ],
    }
