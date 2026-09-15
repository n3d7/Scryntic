"""Audit failures and incomplete coverage must never look like a clean result."""

import pytest

from scripts.audit import expected_packages, validate_report


def test_pinned_requirements_and_markers() -> None:
    assert expected_packages("Example_Pkg==1.2 \\\n    --hash=sha256:abc\n") == {
        ("example-pkg", "1.2")
    }
    assert expected_packages('other==1; python_version < "3.0"') == set()
    with pytest.raises(ValueError):
        expected_packages("unpinned>=1")


def test_complete_audit() -> None:
    validate_report(
        {"dependencies": [{"name": "example", "version": "1", "vulns": []}]},
        {("example", "1")},
    )
    validate_report({"dependencies": []}, set())


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"dependencies": []},
        {"dependencies": [{"name": "example", "skip_reason": "lookup failed"}]},
        {"dependencies": [{"name": "example", "version": "1", "vulns": [{"id": "x"}]}]},
        {"dependencies": [{"name": "example", "version": "2", "vulns": []}]},
        {"dependencies": [{"name": "example", "version": "1"}]},
    ],
)
def test_incomplete_or_vulnerable_audit_fails(report: object) -> None:
    with pytest.raises(ValueError):
        validate_report(report, {("example", "1")})
