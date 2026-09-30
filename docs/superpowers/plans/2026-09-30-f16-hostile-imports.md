# F16 / KER-20 implementation plan

Approved scope: workstation staging, format/schema validation, restricted decoding,
bounded independently checked primitive results, transactional import catalog.
F17 transport/enrollment/chain anchors and F21 model qualification are excluded.

1. Reuse canonical F11 manifests and current raw/normalized Parquet contracts.
   Keep all native imports out of the import coordinator. Extract path decoders
   without changing the collector's trusted publication behavior.
2. Snapshot descriptor-relative regular files into bounded sealed memfds. Check
   hashes and before/after file identity/metadata; remote paths never choose local
   destinations. Only generated digest names reach staging/storage.
3. Fixed Linux Bubblewrap launcher: explicit namespaces, namespace UID/GID,
   immutable input/runtime mounts, no home/config/proc/dev/network, empty env,
   closed inherited FDs, no capabilities. Bootstrap applies hard resource limits
   and seccomp before importing native decoders. No fallback on missing controls.
4. Select/read bounded pipes to a monotonic deadline. Kill/reap on all failures.
   Compare strict canonical success metadata independently with the manifest;
   no Python objects, arbitrary paths, SQL or executable serialization over IPC.
5. F04-private import storage, one owner, durable no-replace opaque objects and
   transactional SQLite registration. Failed validation cannot register rows.
   Preserve F12 workstation receipt evidence alongside opaque remote claims.
   Every inspection reopens sealed snapshots and runs the restricted decoder.
6. Test hostile manifests/files, races, bounds, timeouts, IPC and actual host
   controls; cross-check SOFA advice against Context7 and primary documentation.
7. Fresh project gates, SonarQube analysis and contextual findings review, final
   diff review, commit/push, KER-20 In Review and PR. No merge or auto-merge.
