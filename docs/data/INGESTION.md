# Durable ingestion (F05)

`scryntic.ingestion.DurableIngestor` is the local durability boundary between a
source adapter and later normalization. It accepts an already bounded
`RawEnvelope`, assigns an `IngestionId`, and returns a `RawRecord` only after the
SQLite transaction has committed.

## Ownership and paths

The ingestor consumes an F04 `Installation` and uses its private state directory.
It creates `ingestion.lock` and `ingestion.sqlite3` as owner-only regular files.
It rejects symlinks, hardlinks, unexpected owners and unsafe modes.

The lock uses Linux `flock(LOCK_EX | LOCK_NB)`. It is acquired before SQLite is
opened and its descriptor remains open until the writer stops. This prevents two
cooperating Scryntic processes from opening writer connections for one state
directory. It is not an authorization mechanism or a defense against root or
malicious code running as the service UID.

The schema metadata binds a state directory to one producer identity. Restarts
may use a new epoch, but a different producer must use a different state
directory. Startup rejects mismatched producers, foreign-producer rows,
incompatible version-1 table definitions and unexpected tables, indexes, views
or triggers.

One worker thread owns the only SQLite connection. Status queries, reads and
writes are sent to that owner through a bounded queue. A full queue raises
`IntakeFull`; no queued item is evicted. Queue admission is pending work, not an
acknowledgement. Graceful close drains already admitted requests before releasing
the ownership lock. Concurrent close callers wait for the same serialized shutdown;
an interrupted caller can retry without prematurely releasing ownership.

## Transactions and durability

Python opens the connection with `autocommit=True`, which leaves SQLite in its
native autocommit mode until SQL starts a transaction. Each envelope uses exactly:

1. `BEGIN IMMEDIATE`;
2. one `INSERT` that obtains the monotonic offset;
3. `COMMIT`.

On failure, `ROLLBACK` runs only when `Connection.in_transaction` confirms that
the transaction remains open. The implementation does not use Python's implicit
legacy transaction behavior or mix SQL control with `Connection.commit()` and
`Connection.rollback()`. If rollback fails or leaves a transaction open, the sole
worker fails permanently, rejects queued and future requests, closes the connection
and requires a clean restart.

Startup requests `journal_mode=WAL` and `synchronous=FULL`, then reads both values
back and fails if they did not take effect. It also validates the schema version
and runs `quick_check`. The accepted high-water mark is derived from committed raw
rows, never from in-memory queue state. Identical deliveries are separate rows and
receive separate offsets. Before acknowledging, the writer reads the inserted row
inside the same transaction and verifies its identity, complete envelope and hash.

SQLite performs normal WAL recovery when the sole connection opens. Scryntic does
not copy, delete or manually repair live database, WAL or SHM files. It does not
open a second connection for reads or checkpoints. The managed CPython 3.12.14
used for F05 validation embeds SQLite 3.53.1. Earlier affected SQLite versions are
safe from the WAL-reset race only while the one-connection invariant holds; any
future multi-connection design must revisit the runtime requirement first.

## Scope boundary

The spool preserves original bytes, hash, source metadata, typed subject identity,
source timestamps and receipt-clock evidence. It does not normalize data, publish
archives, prune records, perform network I/O or own F06 processing checkpoints.
