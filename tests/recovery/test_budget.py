import pytest

from scryntic.recovery.budget import SharedBudget


def test_backfill_preserves_live_count_and_byte_reserve() -> None:
    budget = SharedBudget(records=4, bytes_=100, live_records=1, live_bytes=30)
    backfill = budget.try_acquire("backfill", 3, 70)
    assert backfill is not None
    assert budget.try_acquire("backfill", 1, 1) is None
    live = budget.try_acquire("live", 1, 30)
    assert live is not None
    assert budget.try_acquire("live", 1, 1) is None
    backfill.release()
    live.release()
    assert budget.used == (0, 0)
    with pytest.raises(RuntimeError):
        live.release()
