"""Execute the distribution smoke verifier against the real CLI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def test_console_smoke_runs_offline_workflows_and_masks_errors(tmp_path: Path) -> None:
    from scripts.verify_cli_install import verify_console

    command = Path(sys.executable).parent / "scryntic"
    verify_console(command, "base", tmp_path)


@pytest.mark.skipif(
    os.environ.get("SCRYNTIC_F24_HOST") != "1",
    reason="F24 real-host worker success is unqualified; set SCRYNTIC_F24_HOST=1",
)
def test_analysis_console_uses_real_pull_data_replay_and_model_worker(
    tmp_path: Path,
) -> None:
    from scripts.verify_cli_install import verify_analysis

    verify_analysis(Path(sys.executable).parent / "scryntic", tmp_path)


def test_analysis_console_preserves_failed_worker_outcomes(tmp_path: Path) -> None:
    from scripts.verify_cli_install import verify_analysis

    verify_analysis(
        Path(sys.executable).parent / "scryntic", tmp_path, require_worker_success=False
    )


def test_optional_tui_console_keyboard_navigation_and_quit(tmp_path: Path) -> None:
    pytest.importorskip("textual")
    from scripts.verify_cli_install import _installation, verify_tui_console

    home = tmp_path / "workstation"
    home.mkdir(mode=0o700)
    _installation(home)
    verify_tui_console(Path(sys.executable).parent / "scryntic", home, tmp_path)
