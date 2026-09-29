import subprocess
from pathlib import Path

import pytest

from scryntic.clock.chrony import ChronyStatus, parse_tracking

# chrony 4.x CSV: ref id, address, stratum, reference time, current correction,
# last/RMS offset, frequency/residual/skew, root delay/dispersion, interval, leap.
TRACKING = "A29FC801,162.159.200.1,4,1000.000000000,0.000002000,0.000001000,0.000003000,-13.956,0.002,0.113,0.000006000,0.000004000,64.0,Normal\n"


def test_tracking_uses_system_correction_not_last_offset_and_conservative_error() -> (
    None
):
    value = parse_tracking(TRACKING)
    assert value.synchronized
    assert value.reference_time_ns == 1_000_000_000_000
    assert value.offset_ns == -2000  # positive CSV correction means system slow
    # |offset| + half root delay + dispersion + CSV rounding allowance.
    assert value.uncertainty_ns == 9002
    assert (
        parse_tracking(TRACKING.replace("0.000002000", "-0.000002000")).offset_ns
        == 2000
    )


@pytest.mark.parametrize("leap", ["Not synchronised", "Insert second", "Delete second"])
def test_unsynchronized_or_ambiguous_leap_report_is_not_healthy(leap: str) -> None:
    assert not parse_tracking(TRACKING.replace("Normal", leap)).synchronized


@pytest.mark.parametrize("reference", ["00000000", "7F7F0101"])
def test_local_or_missing_reference_cannot_claim_external_synchronization(
    reference: str,
) -> None:
    assert not parse_tracking(TRACKING.replace("A29FC801", reference)).synchronized


@pytest.mark.parametrize(
    "bad",
    [
        "Normal",
        TRACKING + TRACKING,
        TRACKING.replace("0.000002000", "NaN"),
        TRACKING.replace("0.000004000", "-1"),
        TRACKING.replace("64.0", "Infinity"),
        TRACKING.replace(",4,", ",16,"),
        TRACKING.replace("Normal", "Unexpected"),
        TRACKING.replace("1000.000000000", "1e10000"),
        TRACKING.replace("0.000006000", "-0.1"),
    ],
)
def test_malformed_tracking_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_tracking(bad)


def test_real_subprocess_adapter_executes_only_monitoring_and_parses_response(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "chronyc"
    # Exercise argv and the real process boundary, without a daemon or clock mutation.
    executable.write_text(
        "#!/bin/sh\n[ \"$*\" = '-n -c -h 127.0.0.1 tracking' ] || exit 3\nprintf '%s' '"
        + TRACKING
        + "'\n"
    )
    executable.chmod(0o700)
    value = ChronyStatus(executable=executable).read()
    assert value is not None and value.offset_ns == -2000


@pytest.mark.parametrize("body", ["exit 1", "printf 'unparseable'", "sleep 2"])
def test_failed_or_timed_out_status_is_missing_not_fabricated(
    tmp_path: Path, body: str
) -> None:
    executable = tmp_path / "chronyc"
    executable.write_text("#!/bin/sh\n" + body + "\n")
    executable.chmod(0o700)
    assert ChronyStatus(executable=executable, timeout_s=0.05).read() is None


def test_unavailable_executable_is_missing(tmp_path: Path) -> None:
    assert ChronyStatus(executable=tmp_path / "absent").read() is None


def test_timeout_does_not_leak_daemon_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("chronyc", 1, output="sentinel-secret")

    monkeypatch.setattr(subprocess, "run", fail)
    assert ChronyStatus().read() is None
