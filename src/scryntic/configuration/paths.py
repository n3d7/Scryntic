"""Approved Linux path policy and descriptor-based, bounded reads."""

import os
import pwd
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scryntic.configuration.values import BoundaryError, ErrorCode, reference_name


@dataclass(frozen=True, slots=True)
class Installation:
    config_dir: Path
    state_dir: Path
    runtime_dir: Path
    credential_dir: Path
    owner_uid: int
    config_uid: int
    service_mode: bool

    @classmethod
    def service(cls, service_uid: int) -> "Installation":
        if type(service_uid) is not int or service_uid <= 0:
            raise BoundaryError(ErrorCode.UNSAFE_PATH)
        return cls(
            Path("/etc/scryntic"),
            Path("/var/lib/scryntic"),
            Path("/run/scryntic"),
            Path("/etc/scryntic/credentials"),
            service_uid,
            0,
            True,
        )

    @classmethod
    def workstation(
        cls, *, home: Path | None = None, environment: Mapping[str, str] | None = None
    ) -> "Installation":
        uid = os.geteuid()
        base = home if home is not None else Path(pwd.getpwuid(uid).pw_dir)
        env = os.environ if environment is None else environment
        config = Path(env.get("XDG_CONFIG_HOME") or base / ".config") / "scryntic"
        state = Path(env.get("XDG_STATE_HOME") or base / ".local/state") / "scryntic"
        runtime = (
            Path(env["XDG_RUNTIME_DIR"]) / "scryntic"
            if env.get("XDG_RUNTIME_DIR")
            else state / "runtime"
        )
        paths = (config, state, runtime, config / "credentials")
        for path in paths:
            _components(path)
        return cls(*paths, uid, uid, False)


def _components(path: Path) -> tuple[str, ...]:
    # Do not resolve symlinks or normalize away '..'.
    if not path.is_absolute() or ".." in path.parts or len(path.parts) > 128:
        raise BoundaryError(ErrorCode.UNSAFE_PATH)
    return path.parts[1:]


def _directory_mode(info: os.stat_result, uid: int, *, private: bool) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise BoundaryError(ErrorCode.UNSAFE_PATH)
    if mode & 0o7022 or (private and mode != 0o700):
        raise BoundaryError(ErrorCode.UNSAFE_PATH)


@contextmanager
def directory(path: Path, uid: int, *, private: bool) -> Iterator[int]:
    """Pin each parent before opening a child; never authorize a later path reopen."""
    components = _components(path)
    if not components:
        raise BoundaryError(ErrorCode.UNSAFE_PATH)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, component in enumerate(components):
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if index == len(components) - 1:
                _directory_mode(info, uid, private=private)
            else:
                # A root-owned sticky shared ancestor (e.g. /tmp in tests) cannot
                # replace a child owned by another UID. Final roots stay private.
                shared_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
                if info.st_uid not in (0, uid) or (
                    info.st_mode & 0o022 and not shared_sticky
                ):
                    raise BoundaryError(ErrorCode.UNSAFE_PATH)
        yield fd
    finally:
        os.close(fd)


def validate_directories(installation: Installation) -> None:
    failure = False
    try:
        if os.geteuid() != installation.owner_uid or (
            installation.service_mode
            and installation.config_uid == installation.owner_uid
        ):
            raise BoundaryError(ErrorCode.UNSAFE_PATH)
        for path, uid, private in (
            (
                installation.config_dir,
                installation.config_uid,
                not installation.service_mode,
            ),
            (installation.state_dir, installation.owner_uid, True),
            (installation.runtime_dir, installation.owner_uid, True),
        ):
            with directory(path, uid, private=private):
                pass
    except (OSError, BoundaryError):
        failure = True
    if failure:
        raise BoundaryError(ErrorCode.UNSAFE_PATH)


def _file_mode(info: os.stat_result, uid: int, *, secret: bool) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise BoundaryError(ErrorCode.UNSAFE_PATH)
    if (secret and mode not in (0o400, 0o600)) or (not secret and mode & 0o7022):
        raise BoundaryError(ErrorCode.UNSAFE_PATH)


def read_file(
    root: Path, name: str, uid: int, *, private: bool, secret: bool, limit: int
) -> bytes:
    """Return bounded bytes; raw OS exception text/context never leaves this boundary."""
    result = _read_attempt(root, name, uid, private=private, secret=secret, limit=limit)
    if result is None:
        raise BoundaryError(
            ErrorCode.INVALID_CREDENTIAL if secret else ErrorCode.UNSAFE_PATH
        )
    return result


def _read_attempt(
    root: Path, name: str, uid: int, *, private: bool, secret: bool, limit: int
) -> bytes | None:
    try:
        if secret:
            reference_name(name)
        elif name != "config.toml":
            raise BoundaryError(ErrorCode.UNSAFE_PATH)
        with directory(root, uid, private=private) as parent:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=parent,
            )
            try:
                before = os.fstat(fd)
                _file_mode(before, uid, secret=secret)
                if before.st_size > limit:
                    return None
                data = bytearray()
                while len(data) <= limit:
                    chunk = os.read(fd, min(8192, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                after = os.fstat(fd)
                _file_mode(after, uid, secret=secret)
                if len(data) > limit or (
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    return None
                return bytes(data)
            finally:
                os.close(fd)
    except (OSError, BoundaryError):
        return None
