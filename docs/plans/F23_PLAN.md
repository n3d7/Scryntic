# F23 / KER-27 implementation scope

Base: main `aeff60ee76190ab024aaebd22dca1e4b14db21da`, after F22 qualification.

1. Compose existing durable collection/recovery, clock, publication and job
   services in a runnable foreground process, with optional enrolled F17 pull.
2. Keep admission, actual execution ownership and durable completion separate;
   validate/recover before intake and bound the shared shutdown budget.
3. Publish closed, sanitized logs and bounded cached health across subsystems.
4. Exercise graceful and forced stops at durable checkpoints; test stale feeds,
   clock/storage/normalization faults, backpressure, jobs and cancellation.
5. Run unchanged project/negative gates, relevant host integration, Semgrep,
   Trivy and full SonarQube analysis; review findings against source and tests.
6. Record actual evidence and limitations, then create a dedicated branch,
   commit/push and unmerged review PR. Leave KER-27 In Review after verification.

Preserve F12/F15/F17/F20 guarantees and F21/F22 isolation. F24–F27 workflows,
hot reload and architectural/dependency changes remain deferred.
