import os
from pathlib import Path

import pytest

from tests.archive.helpers import installation


def test_reservation_access_audit_restart_and_committed_truncation(
    tmp_path: Path,
) -> None:
    from scryntic.research.ledger import ResearchDenied, ResearchLedger, Reservation

    root = installation(tmp_path)
    with ResearchLedger(root, clock=lambda: 10) as ledger:
        ledger.reserve(Reservation("a" * 64, 100, 200, 90))
        ledger.check_dataset("b" * 64, {"time_range_ns": [1, 80]}, "inspect")
        with pytest.raises(ResearchDenied):
            ledger.check_dataset("c" * 64, {"time_range_ns": [80, 120]}, "export")
        with pytest.raises(ResearchDenied):
            ledger.check_dataset("d" * 64, {"time_range_ns": None}, "inspect")
        ledger.trial("trial-a", "e" * 64, "registered", 0)
        ledger.trial("trial-a", "e" * 64, "started", 1)
        ledger.trial("trial-a", "e" * 64, "failed", 1)
        count = len(ledger.events)
    with ResearchLedger(root, clock=lambda: 11) as ledger:
        assert len(ledger.events) == count
        assert ledger.reservation.policy_sha256 == "a" * 64
        ledger.trial("trial-a", "e" * 64, "failed", 1)
        assert len(ledger.events) == count
        assert sum(e["kind"] == "access_denied" for e in ledger.events) == 2
    path = root.state_dir / "research-ledger.jsonl"
    data = path.read_bytes()
    path.write_bytes(data[: data.rfind(b"\n", 0, -1) + 1])
    with pytest.raises(ValueError):
        ResearchLedger(root)


def test_unanchored_complete_append_recovers_but_partial_tail_fails(
    tmp_path: Path,
) -> None:
    from scryntic.research.ledger import ResearchLedger, Reservation

    root = installation(tmp_path)
    with ResearchLedger(root, clock=lambda: 10) as ledger:
        ledger.reserve(Reservation("a" * 64, 100, 200, 90))
    anchor = (root.state_dir / "research-ledger.anchor").read_bytes()
    with ResearchLedger(root, clock=lambda: 11) as ledger:
        ledger.trial("trial-a", "b" * 64, "registered", 0)
    (root.state_dir / "research-ledger.anchor").write_bytes(anchor)
    with ResearchLedger(root, clock=lambda: 12) as ledger:
        assert len(ledger.events) == 2
    with (root.state_dir / "research-ledger.jsonl").open("ab") as f:
        f.write(b'{"partial":')
        f.flush()
        os.fsync(f.fileno())
    with pytest.raises(ValueError):
        ResearchLedger(root)


def test_quota_reservation_replacement_and_unknown_schema_fail_closed(
    tmp_path: Path,
) -> None:
    from scryntic.research.ledger import ResearchLedger, Reservation

    root = installation(tmp_path)
    with ResearchLedger(root, clock=lambda: 10) as ledger:
        ledger.reserve(Reservation("a" * 64, 100, 200, 90))
        replacement = Reservation("b" * 64, 100, 200, 90)
        with pytest.raises(ValueError):
            ledger.reserve(replacement)
        for n in range(8):
            ledger.trial(f"trial-{n}", "b" * 64, "registered", 0)
        with pytest.raises(ValueError):
            ledger.trial("trial-9", "b" * 64, "registered", 0)
    path = root.state_dir / "research-ledger.anchor"
    path.write_bytes(path.read_bytes().replace(b'"major":1', b'"major":9'))
    with pytest.raises(ValueError):
        ResearchLedger(root)
