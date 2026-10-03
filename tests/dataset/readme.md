# Dataset tests

Checks deterministic selection, revision and duplicate handling, snapshot output, provenance, and explicit failure paths.

F18 fixtures exercise real restricted accepted-input builds and rereads, temporal
uncertainty/cutoffs, coverage, gap-aware features/labels, old schema references,
immutable pins, durability, tampering and coordinator decoder exclusion.

Guard fixtures cover canonical/closed IPC, rejected coerced values, invalid policy
and cutoff combinations, refusal before input/native access, independently checked
worker identity/cardinality, and corrupted-pin read/recreate/retirement failures.
