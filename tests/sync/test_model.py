import pytest

from scryntic.sync.model import Anchor, Enrollment, PullLimits, RemoteEntry


def test_anchor_requires_explicit_genesis() -> None:
    assert Anchor("producer-a", "epoch-a", 0, "0" * 64).sequence == 0
    with pytest.raises(ValueError):
        Anchor("producer-a", "epoch-a", 0, "a" * 64)


@pytest.mark.parametrize("name", ["../escape", "/root", ".", "..", "x\x00"])
def test_remote_entries_cannot_choose_paths(name: str) -> None:
    with pytest.raises(ValueError):
        RemoteEntry(name, 0o100444, 10)


def test_limits_reject_unbounded_transfers() -> None:
    with pytest.raises(ValueError):
        PullLimits(chunk_bytes=1024 * 1024)


def test_enrollment_is_not_implicit() -> None:
    with pytest.raises(ValueError):
        Enrollment(
            "server-a",
            "127.0.0.1",
            22,
            "reader",
            "/published",
            "",
            "producer-a",
            "epoch-a",
        )
