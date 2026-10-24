import pytest

from scryntic.research.baseline_results import validate_result


@pytest.mark.parametrize(
    "value", [{}, {"scores": {}}, {"final_labels_accessed": True}, {"mae": "nan"}]
)
def test_partial_or_nonfinite_worker_documents_are_not_results(
    value: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_result(value, "zero", "a" * 64)
