import base64
from pathlib import Path

from scryntic.sync.model import Enrollment, RemoteEntry

HOST_KEY = "ssh-ed25519 " + base64.b64encode(
    b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"k" * 32
).decode("ascii")


def enrollment(epoch: str = "epoch-a") -> Enrollment:
    return Enrollment(
        "server-a",
        "127.0.0.1",
        22,
        "reader",
        "/published",
        HOST_KEY,
        "collector-a",
        epoch,
    )


class PathRemote:
    def __init__(self, root: Path, endpoint: Enrollment) -> None:
        self.root = root
        self.enrollment = endpoint
        self.reads: list[tuple[str, int, int]] = []
        self.interrupt_after: int | None = None
        self.object_reads = 0

    async def entries(self, relative: str) -> tuple[RemoteEntry, ...]:
        return tuple(
            RemoteEntry(p.name, p.lstat().st_mode, p.lstat().st_size)
            for p in (self.root / relative).iterdir()
        )

    async def read(self, relative: str, offset: int, count: int) -> bytes:
        self.reads.append((relative, offset, count))
        if relative.startswith("objects/"):
            self.object_reads += 1
            if self.interrupt_after == self.object_reads:
                raise ConnectionError("private remote diagnostic")
        with (self.root / relative).open("rb") as source:
            source.seek(offset)
            return source.read(count)
