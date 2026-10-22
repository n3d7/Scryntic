# F12 clock-quality monitoring

F12 qualifies the **Linux x86_64 + chrony 4.9** collector/workstation profile.
The operator runs chronyd and supplies synchronization; Scryntic never sets the
clock. `ChronyStatus` executes only `chronyc -n -c -h 127.0.0.1 tracking` with
a timeout, no shell and no control socket. It returns no evidence when chronyc,
the daemon, or a valid report is unavailable. No root capability, clock-setting
permission or exchange credential is used. Other time services need a separately
qualified status adapter before they can assert `healthy`.

The signed `TimeQuality.offset_ns` is **system wall minus reference time**.
Chrony's CSV `System time` correction has the opposite sign: a positive
correction means the system clock is slow. The raw conservative error radius is
`abs(system correction) + root dispersion + root delay / 2`, rounded outward
with a 2 ns formatting allowance. `Last offset`, RMS offset and the leap flag
alone are not a bound. The reference timestamp is the last processed source
measurement. `Normal` leap status, a non-local source and numerical bounds are
all required for healthy status. These quantities assume the time source is
trustworthy; exchange/source timestamps are separate claims.

`ClockMonitor` compares wall, monotonic and Linux `CLOCK_BOOTTIME` before and
after each status read and against the previous sample. It uses monotonic time
only within its process/session. Startup emits `unknown` until a second
consistent sample. A step, suspend, regressing reference measurement or long
sampling gap changes the epoch and
invalidates old evidence until chrony reports a later reference measurement.
A new reader/process gets a new session and epoch; a changed session in a live
monitor also invalidates old evidence. Smaller observed wall/monotonic
differences widen uncertainty until refreshed. Missing, stale, future-dated or
unrepresentable evidence yields `unknown`; unsynchronized or out-of-budget
evidence yields `degraded`. A machine without `CLOCK_BOOTTIME` cannot claim
healthy under this profile. The monitor exposes the latest `ClockHealth` and
logs sanitized status/epoch transitions for alerting; F23 will compose daemon
health reporting.

The `[clock]` settings set per-workload maximum signed offset, total uncertainty,
source-evidence age, detectable wall/monotonic step, sampling gap and assumed
holdover drift in parts per billion. Total uncertainty includes measured offset,
round-trip dispersion/delay, probe and capture spans, unmeasured wall changes,
and age-dependent holdover growth. The drift budget is an operator assumption,
not an assertion proven by the chrony synchronized flag. Default budgets are
conservative. A qualified 4.9 host with approximately 17-minute chrony polls
needs a larger age and uncertainty budget when that workload can tolerate it:

```toml
[clock]
max_offset_ns = 50000000
max_uncertainty_ns = 1000000000
max_evidence_age_ns = 1800000000000
max_step_ns = 50000000
max_sample_gap_ns = 30000000000
holdover_drift_ppb = 500000
```

The choice of these limits is an operational tolerance, not proof of physical
UTC. On 2026-09-29, an unprivileged read on the qualified host returned
`Normal`, about -0.51 ms signed host offset and 24.9 ms raw error radius; the
measurement was about 898 seconds old. Two immediate `ClockMonitor` samples
under the above profile transitioned `unknown → healthy`, with an age-grown
uncertainty of about 486 ms. Values vary with chrony updates; recheck on the
actual deployment host and workload. Reproduce without changing the clock:

```sh
chronyc -n -c -h 127.0.0.1 tracking
```

The raw journal and immutable archive keep `ClockSample`, epoch, original bytes,
source timestamps and offsets even while clock quality is unknown/degraded.
Status does not silently turn bad receipts into good ones. Use a **fresh**
`ClockMonitor.finality_ready(boundary_ns)` sample for wall-derived finality,
after applying the source's own finality policy. The earliest plausible current
time must be strictly after the closing boundary; a healthy flag by itself is
insufficient. The F12 strict as-observed candle recipe (`1.1`) requires a
bounded cutoff `ClockSample` and includes an input only when its latest
plausible receipt time is at or before the earliest plausible cutoff time.
Excluded inputs remain in the validated publication manifest and are listed by
ingestion identity in `exclusions`; no eligible inputs or an invalid cutoff
fails the build. The original recipe (`1.0`) remains explicit
`historical-reconstruction`: it retains flagged data and makes no trustworthy
as-observed or latency claim. Analysis consumers must not relabel it as strict
observation timing.

On a process restart, existing recorded samples remain evidence of their own
capture intervals, but the new monitor never reuses prior monotonic deadlines
or epoch state. During a step or suspend, the affected interval stays uncertain;
we do not reconstruct its true offset from later synchronization. Chrony's
reported bound depends on the declared host/source trust assumptions and is
not proof against a compromised time source. The adapter does not supply a
bound from systemd-timesyncd's boolean synchronized status.

References: [chronyc 4.9 tracking/CSV and error-bound formula](https://chrony-project.org/doc/4.9/chronyc.html),
[Python 3.12 wall and monotonic clocks](https://docs.python.org/3.12/library/time.html),
[Linux `CLOCK_BOOTTIME`](https://man7.org/linux/man-pages/man2/clock_gettime.2.html).
