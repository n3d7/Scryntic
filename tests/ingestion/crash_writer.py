"""Subprocess fixture: exit immediately after observing durable acknowledgements."""

import os
import sys
from pathlib import Path

from scryntic.configuration.paths import Installation
from scryntic.domain.raw import RawEnvelope
from scryntic.domain.time import ClockSample, TimeQuality
from scryntic.ingestion.sqlite_spool import DurableIngestor


def main(root: Path) -> None:
    uid = os.geteuid()
    installation = Installation(
        root / "config",
        root / "state",
        root / "runtime",
        root / "config" / "credentials",
        uid,
        uid,
        False,
    )
    ingestor = DurableIngestor(
        installation,
        producer="collector-a",
        epoch="boot-a",
        capacity=1,
        max_payload_bytes=1024,
    )
    last_offset = 0
    for sequence in (1, 2):
        envelope = RawEnvelope(
            source="fake-source",
            stream="events",
            channel="public",
            adapter_version="1.0",
            receipt=ClockSample(
                sequence, sequence, "session-a", TimeQuality("clock-a")
            ),
            payload=f"event-{sequence}".encode(),
            payload_limit=1024,
            source_sequence=sequence,
        )
        last_offset = ingestor.accept(envelope).identity.offset
    os.write(sys.stdout.fileno(), f"{last_offset}\n".encode())
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
