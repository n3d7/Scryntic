# Configurable ML feature preparation

Implemented scope: opt-in, features-only preparation over existing accepted imports
and the [F18 dataset service](../DATASETS.md). The same typed request is available
through CLI and TUI. It does not download data, implement a Binance adapter,
train models, generate future labels or admit a source into PI00.

Source-use decisions are configuration, independent of exchange decoding and
the immutable dataset format. Replacing a source still requires an accepted,
qualified adapter/import with its own provenance; configuration cannot turn a
Bybit record into a Binance record. Existing F18 restrictions, including mixed
producer rejection, continue to apply.

## Configure a source-use decision

Inspect each accepted input using its exact manifest hash:

```sh
scryntic data inspect --manifest-sha256 MANIFEST_SHA256
```

Use the returned `partition.source` as `source_id`. This is the provenance source
key, not a display name or an assumed venue-wide permission. Keep each venue,
category and instrument identity intact. This implementation does not infer
original historical availability; reconstructed history remains reconstructed
history under the existing timing mode.

Place a JSON file named `research-policy` in the installation's protected
`config_dir`. The leaf has no filename extension and must satisfy the existing
configuration ownership/mode checks; on a workstation it is owner-only `0600`.
No credential belongs in this file. A safe initial template is:

```json
{
  "schema": {"name": "scryntic.source-use-policy", "major": 1, "minor": 0},
  "policy_id": "private-research-v1",
  "scope": "private-noncommercial-research",
  "sources": [
    {
      "source_id": "REPLACE_WITH_ACCEPTED_BYBIT_SOURCE_ID",
      "status": "pending",
      "allow_storage": false,
      "allow_feature_derivation": false,
      "evidence_sha256": null
    },
    {
      "source_id": "REPLACE_WITH_ACCEPTED_BINANCE_SOURCE_ID",
      "status": "pending",
      "allow_storage": false,
      "allow_feature_derivation": false,
      "evidence_sha256": null
    }
  ]
}
```

Replace placeholders with exact source keys. Leave unresolved sources pending.
For an operator-approved permitted use, record `status: "permitted"`, both
permissions as `true`, and the SHA-256 of the retained evidence supporting that
decision. This is an operator attestation and evidence reference, not an
automatic licence verifier. A fabricated digest provides no permission. This
change has not marked Bybit or Binance permitted or frozen the PI00 policy.

Missing, pending, denied or partially permitted sources fail closed before
feature processing or dataset publication. All selected sources must pass.
Unknown schema versions, extra fields, duplicate source keys, coercible strings
instead of booleans and permitted decisions without an evidence digest are
rejected. The file is bounded to 64 KiB and 64 grants.

## Build and retain the evidence bundle

```sh
scryntic dataset build --manifests MANIFEST_SHA256 \
  --usage-policy-leaf research-policy \
  --preparation-destination prepared-v1
```

Both optional arguments must be supplied together. Existing recipe controls,
such as `--lag-steps 24`, remain available. The new path requires
`label_horizon_steps = 0`; a positive label horizon is rejected. As-observed
requests still require the existing full cutoff evidence. Preparation is not
training admission or access to final evaluation labels.

The existing durable exporter writes under
`<installation.state_dir>/exports/prepared-v1/`:

| File | Meaning |
| --- | --- |
| `dataset.manifest.json` | Exact existing F18 immutable dataset manifest. |
| `dataset.parquet` | Existing bounded feature snapshot and provenance. |
| `source-use-policy.json` | Canonical copy of the decision used for this build. |
| `preparation.json` | Versioned receipt binding input/source IDs, recipe, dataset manifest and policy SHA-256. |
| `complete.json` | Existing exporter completion marker, written last with file hashes. |

Changing only the source-use policy changes its evidence/receipt hash without
rewriting the F18 dataset identity. The existing dataset pins and immutable
accepted inputs are preserved. The result reports policy and receipt hashes.
Only a bundle with a valid completion marker is a completed export.

Exports never overwrite an existing destination. After interruption, an
incomplete destination remains incomplete; retry with a new destination leaf.
An already published immutable dataset can be reused on restart. Revoking a
permission denies subsequent opt-in preparations, including cached inputs; it
does not erase prior archives or exports. Any legally required deletion is a
separate operator retention procedure, not permission to rewrite provenance.

## Compatibility and current limits

Omitting both new arguments retains the Foundation v1 workflow and historical
identities. This opt-in gate is **not retroactive global enforcement** across
legacy ingestion, replay or job paths. Do not treat a successful legacy command
as a source-rights or training approval. This task enables a replaceable
preparation mechanism, not unrestricted ML execution.

No future-return target generation, fitting, ablation or tournament is admitted
by this capability. The complete PI00 scientific/source policy and PI01
trial/holdout safeguards remain required before new research execution; see
the gate record and
execution checkpoint.

Focused tests cover strict policy parsing, source denial before publication,
label rejection, unchanged v1 identities, CLI request mapping, durable export,
restart, revocation and partial-write recovery. The tests use synthetic fixtures
and mocked CLI boundaries, not live exchange requests or real permissions.
