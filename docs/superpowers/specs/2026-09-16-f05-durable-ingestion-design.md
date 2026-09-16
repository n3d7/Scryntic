# F05 Durable Ingestion Design

## Scope

F05 adds one Linux-local SQLite spool behind the existing `RawEnvelope` and
`RawRecord` contracts. It assigns a distinct monotonic `IngestionId` to every
delivery, including byte-identical repeats. It does not normalize, publish,
prune, retrieve network data, or implement F06 processing checkpoints.

## Ownership and intake

`DurableIngestor` owns a bounded in-memory queue and one worker thread. That
thread owns the only SQLite connection for the object's lifetime. Submission
either enters the bounded queue or fails explicitly with `IntakeFull`; it never
evicts another item. A caller receives a `RawRecord` only after the worker has
successfully committed it. Queue admission alone is not durable acceptance.
Concurrent shutdown callers wait for one serialized close operation; ownership is
released only after the writer thread and SQLite connection stop.

Before SQLite is opened, the ingestor opens a fixed lock file in the validated
F04 state directory, acquires `flock(LOCK_EX | LOCK_NB)`, and retains its file
descriptor until shutdown. Failure raises `WriterOwned`. This lock is
cooperative ownership between Scryntic processes, not a security boundary.

## SQLite durability

The connection is opened with Python `sqlite3` `autocommit=True`. This disables
Python-managed implicit transactions while leaving SQL transaction control
available. Each acceptance executes `BEGIN IMMEDIATE`, one insert, and SQL
`COMMIT`; failures issue SQL `ROLLBACK` only when `in_transaction` is true.
Tests observe `Connection.in_transaction` across success and injected commit
failure. Production code never mixes these statements with `Connection.commit`
or `Connection.rollback`.

A rollback failure or residual open transaction permanently fails the writer. The
current and queued operations fail explicitly, the sole connection closes, and
the spool must be restarted before accepting more work.

Startup sets and reads back `journal_mode=WAL` and `synchronous=FULL`, rejects
unexpected values, validates schema version and database integrity, and reads
the committed high-water mark. It relies on SQLite's WAL recovery and never
copies, deletes, checkpoints through another connection, or repairs WAL/SHM
files. The runtime SQLite version is reported in status. SQLite 3.51.2 contains
the WAL-reset bug, but that bug requires concurrent connections writing or
checkpointing; F05's one-connection invariant and pre-open ownership lock avoid
that trigger. A later design that adds connections must re-evaluate the minimum
SQLite version first.

## Persistent representation

Schema version 1 stores the assigned producer, epoch and offset plus every
field needed to reconstruct the immutable envelope: source/stream/channel,
adapter version, receipt and time-quality evidence, optional typed subject,
optional source time/event identity/sequence, payload bytes, and payload hash.
The database uses a strict table and assigns offsets transactionally. Payload
hashes and reconstructed contract values are verified when records are read.

Startup never advances progress. The accepted high-water mark is the highest
committed row for the producer, or zero when empty. Schema mismatch, integrity
failure, invalid stored data, unsafe state paths, failed pragmas and ownership
conflicts fail closed.

The schema metadata binds one producer to the state directory while allowing new
epochs after restart. Startup compares the complete owned schema and rejects
unexpected triggers, views, indexes, tables or incompatible definitions. Before
commit, the writer reads back the inserted row in the same transaction and verifies
the complete `RawRecord`; a suppressed or altered insert cannot be acknowledged.

## Validation

Focused tests use real SQLite files. They cover explicit transaction state,
commit failure without offset advancement, repeated deliveries, queue pressure,
cooperative competing-writer rejection, restart recovery, and a subprocess that
exits abruptly after observing an acknowledgement. Malformed schemas, suppressing
triggers, producer substitution, rollback failure and concurrent shutdown are also
covered. The existing full quality, packaging and dependency gate remains required.
