import os
from hashlib import sha256
from pathlib import Path

import pytest

from scryntic.sync.model import PullError, PullLimits
from scryntic.sync.staging import Staging
from tests.normalization.helpers import installation


def test_partial_resume_survives_reopen(tmp_path: Path) -> None:
    root = installation(tmp_path / "local")
    data = b"opaque-object-bytes"
    name = sha256(data).hexdigest()
    with Staging(root) as stage:
        with stage.partial(name, len(data)) as fd:
            stage.append(fd, data[:6])
    with Staging(root) as stage:
        with stage.partial(name, len(data)) as fd:
            assert os.fstat(fd).st_size == 6
            stage.append(fd, data[6:])
            stage.finish(fd, name, len(data))
        assert stage.has(name, len(data))
        assert (stage.path / name).read_bytes() == data


@pytest.mark.parametrize("attack", ["symlink", "fifo", "hardlink"])
def test_unsafe_partial_never_touches_target(tmp_path: Path, attack: str) -> None:
    root = installation(tmp_path / "local")
    target = tmp_path / "outside"
    target.write_bytes(b"sentinel")
    name = sha256(b"payload").hexdigest()
    with Staging(root) as stage:
        entry = stage.path / (name + ".part")
        if attack == "symlink":
            entry.symlink_to(target)
        elif attack == "fifo":
            os.mkfifo(entry, mode=0o600)
        else:
            os.link(target, entry)
        with pytest.raises(PullError):
            with stage.partial(name, 7):
                pass
    assert target.read_bytes() == b"sentinel"


def test_corrupt_complete_download_cannot_publish(tmp_path: Path) -> None:
    root = installation(tmp_path / "local")
    name = sha256(b"good").hexdigest()
    with Staging(root) as stage:
        with stage.partial(name, 4) as fd:
            stage.append(fd, b"evil")
            with pytest.raises(PullError):
                stage.finish(fd, name, 4)
        assert not (stage.path / name).exists()


def test_staging_quota_checks_partial_bytes(tmp_path: Path) -> None:
    root = installation(tmp_path / "local")
    limits = PullLimits(max_staging_bytes=3)
    with Staging(root, limits) as stage:
        with pytest.raises(PullError):
            with stage.partial(sha256(b"four").hexdigest(), 4):
                pass


def test_crash_after_no_replace_link_recovers_same_inode(tmp_path: Path) -> None:
    root = installation(tmp_path / "local")
    data = b"complete opaque bytes"
    name = sha256(data).hexdigest()
    with Staging(root) as stage:
        with stage.partial(name, len(data)) as fd:
            stage.append(fd, data)
            os.fchmod(fd, 0o400)
            os.link(stage.path / (name + ".part"), stage.path / name)
    with Staging(root) as stage:
        assert not (stage.path / (name + ".part")).exists()
        assert stage.has(name, len(data))


def test_replaced_partial_name_cannot_publish_another_inode(tmp_path: Path) -> None:
    root = installation(tmp_path / "local")
    data = b"good"
    name = sha256(data).hexdigest()
    outside = tmp_path / "outside"
    outside.write_bytes(b"sentinel")
    with Staging(root) as stage:
        with pytest.raises(PullError):
            with stage.partial(name, len(data)) as fd:
                stage.append(fd, data)
                partial = stage.path / (name + ".part")
                partial.unlink()
                partial.symlink_to(outside)
                stage.finish(fd, name, len(data))
        assert not (stage.path / name).exists()
    assert outside.read_bytes() == b"sentinel"
