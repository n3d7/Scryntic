"""Shared storage must preserve the runtime contract type checks of v1 callers."""

from scryntic.jobs.contracts import JobAttempt, JobRecord, JobState
from tests.jobs.helpers import job


def test_v1_attempt_and_record_remain_runtime_classes() -> None:
    request = job()
    attempt = JobAttempt(request, 1, "a" * 32)
    record = JobRecord(
        request, JobState.RUNNING, 1, "a" * 32, None, None, request.submitted_ns
    )
    assert isinstance(attempt, JobAttempt)
    assert isinstance(record, JobRecord)
    assert isinstance(record.execution(), JobAttempt)
