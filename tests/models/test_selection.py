import sys
from pathlib import Path

import pytest

from scryntic.models.selection import ModelSelection, supported_models


def test_selection_is_data_and_does_not_import_vendor_runtime(tmp_path: Path) -> None:
    path = tmp_path / "model.toml"
    path.write_text('[model]\nselected = "fake-persistence"\n')
    selection = ModelSelection.read(path)
    assert selection.selected == "fake-persistence"
    assert "timesfm-2.5" in supported_models()
    assert "timesfm" not in sys.modules
    assert "torch" not in sys.modules


@pytest.mark.parametrize(
    "content",
    [
        '[model]\nselected = "some.module:run"\n',
        '[model]\nselected = "fake-persistence"\nshell = "id"\n',
        '[model]\nselected = "timesfm-2.5"\n',
        '[model]\nselected = "fake-persistence"\n[extra]\nvalue = 1\n',
    ],
)
def test_selection_rejects_unknown_or_incomplete_configuration(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "model.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        ModelSelection.read(path)
