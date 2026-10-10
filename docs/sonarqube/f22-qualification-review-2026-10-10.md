# F22 qualification: seven Sonar findings reviewed

Scope: KER-26, candidate `c8a920d44838c3925928f8706adfddebfe3dd14c` plus
the separately validated forecast-artifact metadata repair. The seven finding
locations are unchanged by that repair. The read-only Sonar MCP refreshed all
seven OPEN findings on 2026-10-10, page 1 of 1. These are default-project
analysis findings, not a published PR analysis.

**Applied disposition: FALSE_POSITIVE for each finding below.** The user
explicitly authorized all seven statuses and the reviewed comments, then
resumed after a pause. The separate authenticated local HTTP API applied
exactly these seven comments/transitions; read-only verification confirmed
every resolution. No thresholds,
exclusions, rule severities or source suppressions were changed. Server changes
require a separately authorized review mechanism under `docs/QUALITY.md`.

| Issue key | Rule and location | Source evidence and proposed comment |
| --- | --- | --- |
| `2b414d4b-c27b-45e4-9fc9-b268d2b77f6e` | python:S5443, model_worker/controls.py:151 | This is an intentional negative isolation probe, not application temporary storage. The fixed /tmp path must reject creation before provider execution. `_write_scratch` uses exclusive `tempfile.mkstemp`, unlinks/closes any unexpectedly created file, and reports successful creation as isolation failure. Effective host controls and both F21/F22 positive probes passed on this installation; writable data is restricted to the separate bounded private mounts. Removing this probe would reduce isolation verification. |
| `da6e63ca-392d-4c89-a3e9-1cacef9e9fc5` | python:S5443, model_worker/controls.py:151 | This is an intentional negative isolation probe, not application temporary storage. The fixed /var/tmp path must reject creation before provider execution. `_write_scratch` uses exclusive `tempfile.mkstemp`, unlinks/closes any unexpectedly created file, and reports successful creation as isolation failure. Effective host controls and both F21/F22 positive probes passed on this installation; writable data is restricted to the separate bounded private mounts. Removing this probe would reduce isolation verification. |
| `f000854a-71df-433b-90f0-55255bbbbaaf` | python:S7497, model_worker/launcher.py:316 | The nested handler delays repeated cancellation only while joining the already-started snapshot thread. The enclosing CancelledError handler re-raises after the thread finishes and its exception is observed. Immediate propagation from the nested handler would let the thread recreate staging after cleanup. `test_snapshot_cancellation_joins_thread_and_preserves_cancellation` covers repeated cancellation with both successful and failing snapshots; both profiles also passed actual cancellation/restart host tests. |
| `3b84e4a2-e70f-48c8-9463-a4850d9e6387` | python:S2612, models/artifacts.py:80 | Mode 0444 is intentional for verified model/runtime snapshot files bound read-only into a worker with a separate dynamic UID. In the production call path these files are beneath the coordinator's private 0700 TemporaryDirectory, inaccessible to other host users. The worker receives explicitly selected mounts only. Copying uses exclusive no-follow creation, bounded hashing and identity checks; failed copies are removed. The artifacts are data-only model/runtime inputs, not credentials. Changing to 0400 would prevent the separate worker UID from reading admitted inputs. |
| `b08463be-4c60-4dff-b777-053e79ed12f2` | python:S8997, tests/model_worker/test_launcher.py:230 | The test already uses the monkeypatch fixture: `monkeypatch.setattr(sys, "path", sys.path.copy())`. Production bootstrap mutates the replacement list in place. Monkeypatch restores the original list object at teardown, so the mutation cannot leak into other tests. Direct mutation of the original sys.path is not being used. |
| `e9ffdfd3-6c7e-46f5-9f7e-52f46b396338` | python:S7497, model_worker/launcher.py:234 | Cancellation is retained in the cancelled flag while a shielded cleanup task is joined; CancelledError is raised after cleanup completes. Cleanup stops/verifies the unit, kills remaining process writers and joins/drains pipes with fixed subprocess and drain timeouts. Immediate propagation would orphan cleanup. Repeated-cancellation subprocess tests assert both cancellation propagation and process disappearance; actual cancellation/restart host probes passed. |
| `e0bf360c-0659-47d2-92d5-c35e7f3e815a` | python:S7497, model_worker/launcher.py:425 | The nested handler joins in-progress subprocess transport creation despite repeated caller cancellation. The enclosing handler then awaits unit/process cleanup and re-raises. This prevents a newly created worker from escaping cancellation before its process handle is available. `test_cancellation_joins_unit_cleanup_even_when_repeated` deliberately cancels during delayed process creation, observes CancelledError and checks that the child no longer exists. Actual cancellation/restart host probes also passed. |

The two S5443 findings share a location containing both fixed paths; the
per-path comments describe the loop's complete behavior without assuming that
Sonar's issue order identifies which literal produced which key.

Validation evidence: all 31 mandatory operator host tests passed on `c8a920d`;
the metadata-repair portable run passed 1,789 tests with the same 31 opt-in host
tests skipped, Ruff, strict mypy, both lock checks, installed packaging and
dependency audits. Host evidence is not relabeled as testing the later dirty
tree. The metadata repair changes artifact serialization only, not launcher,
control, model-adapter, runtime or admission code.

The subsequent full analysis at `2026-10-10T15:40Z` exited 0 with gate OK:
new coverage 81.4%, duplication 0%, new violations 0. Live read-only MCP queries
confirmed zero OPEN/CONFIRMED issues and zero TO_REVIEW hotspots. No reviewed
finding was hidden by a source suppression or rule/quality-policy change.
Ignored local evidence: `state/f22-qualification/metadata-validation/` contains
the approved exact payload, applied-action record, scan log/status and gate
snapshot. The metadata repair is committed as `f0f1e3e`; host/model execution
source is unchanged from the operator-qualified `c8a920d`.

This closes the Sonar qualification blocker. F22 repair publication and operator
review remain separate delivery steps; no F23 work is authorized by this review.
