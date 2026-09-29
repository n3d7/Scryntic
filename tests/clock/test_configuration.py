from pathlib import Path

import pytest

from scryntic.configuration.loader import load_configuration
from scryntic.configuration.values import BoundaryError
from tests.archive.helpers import installation


def test_clock_budgets_load_from_operator_configuration(tmp_path: Path) -> None:
    root = installation(tmp_path)
    path = root.config_dir / "config.toml"
    path.write_text(
        "[clock]\nmax_offset_ns = 123\nmax_evidence_age_ns = 456\nholdover_drift_ppb = 789\n"
    )
    path.chmod(0o600)
    settings = load_configuration(root)
    assert settings.clock.max_offset_ns == 123
    assert settings.clock.max_evidence_age_ns == 456
    assert settings.clock.holdover_drift_ppb == 789


@pytest.mark.parametrize(
    "setting",
    [
        "max_offset_ns = true",
        "max_uncertainty_ns = -1",
        "max_evidence_age_ns = 1.5",
        "max_step_ns = 0",
        "max_uncertainty_ns = 9223372036854775808",
        "holdover_drift_ppb = 1000000000",
        "typo = 1",
    ],
)
def test_invalid_clock_budget_fails_without_echoing_input(
    tmp_path: Path, setting: str
) -> None:
    root = installation(tmp_path)
    path = root.config_dir / "config.toml"
    path.write_text("[clock]\n" + setting + "\n")
    path.chmod(0o600)
    with pytest.raises(BoundaryError) as error:
        load_configuration(root)
    assert setting not in str(error.value)
