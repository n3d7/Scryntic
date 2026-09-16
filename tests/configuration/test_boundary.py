"""Configuration precedence, capability scope and hostile filesystem fixtures."""

import dataclasses
import os
import pickle
import socket
import traceback
from pathlib import Path

import pytest

from scryntic.configuration.credentials import FileCredentials
from scryntic.configuration.loader import load_configuration
from scryntic.configuration.paths import Installation, validate_directories
from scryntic.configuration.values import (
    BoundaryError,
    Capability,
    Configuration,
    Profile,
    SecretReference,
)

SENTINEL = "sentinel-secret-never-in-output-7c49"


@pytest.fixture
def installation(tmp_path: Path) -> Installation:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    result = Installation.workstation(home=home, environment={})
    for path in (
        result.config_dir,
        result.state_dir,
        result.runtime_dir,
        result.credential_dir,
    ):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return result


def config_file(installation: Installation, text: str) -> None:
    path = installation.config_dir / "config.toml"
    path.write_text(text)
    path.chmod(0o600)


def test_defaults_file_overrides_are_deterministic(installation: Installation) -> None:
    config_file(installation, 'profile = "collector"\nlog_level = "warning"\n')
    result = load_configuration(installation, overrides={"log_level": "error"})
    assert result.profile is Profile.COLLECTOR
    assert result.log_level == "error"
    assert result.enabled == frozenset({Capability.PUBLIC_COLLECTION})
    assert result.required_reference(Capability.PUBLIC_COLLECTION) is None
    assert result.diagnostics() == {
        "profile": "collector",
        "log_level": "error",
        "enabled": ["public_collection"],
    }
    assert result == load_configuration(installation, overrides={"log_level": "error"})


@pytest.mark.parametrize(
    "document",
    [
        'profile="invalid"',
        'profile=["collector", "workstation"]',
        'unknown="' + SENTINEL + '"',
        'log_level="' + SENTINEL + '"',
        "[capabilities]\nunknown=true",
        '[credentials]\nexchange="' + SENTINEL + '"',
        'profile="workstation"\n[capabilities]\ntelegram_reports=true',
        'profile="collector"\n[capabilities]\nsynchronization=true',
        '[capabilities]\npublic_collection="yes"',
        'bad = "unterminated' + SENTINEL,
    ],
)
def test_invalid_input_rejected_with_safe_errors_before_network(
    installation: Installation,
    document: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def network_forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Configuration attempted networking")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    config_file(installation, document)
    with pytest.raises(BoundaryError) as caught:
        load_configuration(installation)
    error = caught.value
    assert error.__context__ is None
    output = (
        repr(error)
        + str(error)
        + "".join(traceback.format_exception(error))
        + caplog.text
    )
    output += str(capsys.readouterr())
    assert SENTINEL not in output


def test_invalid_overridden_input_is_not_hidden(installation: Installation) -> None:
    config_file(installation, 'log_level="invalid"')
    with pytest.raises(BoundaryError):
        load_configuration(installation, overrides={"log_level": "info"})
    config_file(installation, "")
    with pytest.raises(BoundaryError):
        load_configuration(installation, overrides={"credentials": SENTINEL})


def test_only_enabled_capability_requires_a_reference(
    installation: Installation,
) -> None:
    config_file(
        installation,
        'profile="collector"\n[credentials.telegram]\nbackend="file"\nname="absent"',
    )
    result = load_configuration(installation)
    assert result.required_reference(Capability.PUBLIC_COLLECTION) is None
    with pytest.raises(BoundaryError):
        result.required_reference(Capability.TELEGRAM_REPORTS)
    # Missing dormant sources are never opened or checked.
    installation.credential_dir.rmdir()
    assert load_configuration(installation) == result
    config_file(
        installation, 'profile="collector"\n[capabilities]\ntelegram_reports=true'
    )
    with pytest.raises(BoundaryError):
        load_configuration(installation)


def test_scoped_file_secret_never_enters_configuration(
    installation: Installation,
) -> None:
    config_file(
        installation,
        'profile="collector"\n[capabilities]\ntelegram_reports=true\n[credentials.telegram]\nbackend="file"\nname="bot-token"',
    )
    (installation.credential_dir / "bot-token").write_text(SENTINEL)
    (installation.credential_dir / "bot-token").chmod(0o400)
    config = load_configuration(installation)
    reference = config.required_reference(Capability.TELEGRAM_REPORTS)
    assert reference is not None
    source = credentials(installation)
    with FileCredentials(
        installation, config, Capability.TELEGRAM_REPORTS
    ).open() as secret:
        view = secret.view()
        assert bytes(view).decode() == SENTINEL
        assert SENTINEL not in repr(secret) + str(secret) + repr(source)
        with pytest.raises(BoundaryError):
            pickle.dumps(secret)
    assert bytes(view) == b"\0" * len(SENTINEL)
    assert SENTINEL not in repr(config) + str(config) + repr(
        dataclasses.asdict(config)
    ) + repr(config.diagnostics())
    with pytest.raises(BoundaryError):
        FileCredentials(installation, config, Capability.SYNCHRONIZATION)
    with pytest.raises(BoundaryError):
        FileCredentials(installation, config, Capability.PUBLIC_COLLECTION)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o700, 0o4600])
def test_unsafe_credentials_fail(installation: Installation, mode: int) -> None:
    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(mode)
    with pytest.raises(BoundaryError) as error:
        credentials(installation).open()
    assert SENTINEL not in str(error.value)


def test_missing_symlink_hardlink_fifo_and_traversal_fail(
    installation: Installation,
) -> None:
    source = credentials(installation)
    with pytest.raises(BoundaryError):
        source.open()
    path = installation.credential_dir / "token"
    other = installation.credential_dir / "other"
    other.write_text(SENTINEL)
    other.chmod(0o600)
    path.symlink_to(other)
    with pytest.raises(BoundaryError):
        source.open()
    path.unlink()
    os.link(other, path)
    with pytest.raises(BoundaryError):
        source.open()
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(BoundaryError):
        source.open()
    with pytest.raises(BoundaryError):
        SecretReference(Capability.TELEGRAM_REPORTS, "file", "../other")


def test_path_ownership_and_substitution_fail(installation: Installation) -> None:
    config_file(installation, "")
    validate_directories(installation)
    installation.state_dir.chmod(0o755)
    with pytest.raises(BoundaryError):
        load_configuration(installation)
    installation.state_dir.chmod(0o700)
    original = installation.state_dir.with_name("old-state")
    installation.state_dir.rename(original)
    installation.state_dir.symlink_to(original)
    with pytest.raises(BoundaryError):
        load_configuration(installation)
    with pytest.raises(BoundaryError):
        validate_directories(
            dataclasses.replace(installation, owner_uid=os.geteuid() + 1)
        )


def test_xdg_and_service_reference_layout(tmp_path: Path) -> None:
    user = Installation.workstation(home=tmp_path, environment={})
    assert user.config_dir == tmp_path / ".config/scryntic"
    assert user.state_dir == tmp_path / ".local/state/scryntic"
    assert user.runtime_dir == user.state_dir / "runtime"
    custom = Installation.workstation(
        home=tmp_path,
        environment={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        },
    )
    assert custom.config_dir == tmp_path / "config/scryntic"
    assert custom.runtime_dir == tmp_path / "run/scryntic"
    with pytest.raises(BoundaryError):
        Installation.workstation(
            home=tmp_path, environment={"XDG_STATE_HOME": "relative"}
        )
    service = Installation.service(12345)
    assert service.config_dir == Path("/etc/scryntic")
    assert service.state_dir == Path("/var/lib/scryntic")
    assert service.runtime_dir == Path("/run/scryntic")
    assert service.config_uid == 0


def credentials(installation: Installation) -> FileCredentials:
    config = Configuration(
        Profile.COLLECTOR,
        "info",
        frozenset({Capability.TELEGRAM_REPORTS}),
        (SecretReference(Capability.TELEGRAM_REPORTS, "file", "token"),),
    )
    return FileCredentials(installation, config, Capability.TELEGRAM_REPORTS)


def test_disabled_capability_cannot_bind_credentials(
    installation: Installation,
) -> None:
    config = Configuration(
        Profile.COLLECTOR,
        "info",
        frozenset({Capability.PUBLIC_COLLECTION}),
        (SecretReference(Capability.TELEGRAM_REPORTS, "file", "token"),),
    )
    with pytest.raises(BoundaryError):
        FileCredentials(installation, config, Capability.TELEGRAM_REPORTS)


def test_public_collection_does_not_open_credentials(
    installation: Installation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Public configuration attempted credential resolution")

    monkeypatch.setattr(FileCredentials, "open", forbidden)
    config_file(installation, 'profile="collector"')
    installation.credential_dir.rmdir()
    result = load_configuration(installation)
    assert result.references == ()
    assert result.required_reference(Capability.PUBLIC_COLLECTION) is None


def test_profile_override_and_transfer_scope(installation: Installation) -> None:
    config_file(installation, 'profile="collector"')
    config = load_configuration(installation, overrides={"profile": "workstation"})
    assert config.profile is Profile.WORKSTATION
    assert config.enabled == frozenset({Capability.LOCAL_ANALYSIS})
    config_file(
        installation,
        '[capabilities]\nsynchronization=true\n[credentials.transfer]\nbackend="file"\nname="transfer-key"',
    )
    config = load_configuration(installation)
    source = FileCredentials(installation, config, Capability.SYNCHRONIZATION)
    with pytest.raises(BoundaryError):
        source.open()
    with pytest.raises(BoundaryError):
        FileCredentials(installation, config, Capability.TELEGRAM_REPORTS)


@pytest.mark.parametrize(
    "mutation", ["writable", "symlink", "oversize", "invalid_utf8"]
)
def test_configuration_file_attacks_fail(
    installation: Installation, mutation: str
) -> None:
    config_file(installation, "")
    path = installation.config_dir / "config.toml"
    if mutation == "writable":
        path.chmod(0o620)
    elif mutation == "symlink":
        original = path.with_name("original")
        path.rename(original)
        path.symlink_to(original)
    elif mutation == "oversize":
        path.write_bytes(b" " * 65537)
    else:
        path.write_bytes(b"\xff")
    with pytest.raises(BoundaryError) as error:
        load_configuration(installation)
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "mutation",
    ["writable_parent", "symlink_parent", "public_root", "empty", "oversize"],
)
def test_credential_path_and_size_attacks_fail(
    installation: Installation, mutation: str
) -> None:
    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    if mutation == "writable_parent":
        installation.config_dir.chmod(0o720)
    elif mutation == "symlink_parent":
        original = installation.credential_dir.with_name("moved")
        installation.credential_dir.rename(original)
        installation.credential_dir.symlink_to(original)
    elif mutation == "public_root":
        installation.credential_dir.chmod(0o755)
    elif mutation == "empty":
        path.write_bytes(b"")
    else:
        path.write_bytes(b"x" * 65537)
    with pytest.raises(BoundaryError) as error:
        credentials(installation).open()
    assert error.value.__context__ is None
    assert SENTINEL not in str(error.value)


def test_file_replacement_during_read_is_rejected(
    installation: Installation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    original_read = os.read
    replaced = False

    def replace_then_read(fd: int, count: int) -> bytes:
        nonlocal replaced
        if not replaced:
            path.rename(path.with_name("original"))
            path.symlink_to("/etc/passwd")
            replaced = True
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", replace_then_read)
    with pytest.raises(BoundaryError):
        credentials(installation).open()
    with pytest.raises(BoundaryError):
        credentials(installation).open()


def test_credential_change_during_read_is_rejected(
    installation: Installation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    original_read = os.read

    def change_then_read(fd: int, count: int) -> bytes:
        path.chmod(0o644)
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", change_then_read)
    with pytest.raises(BoundaryError):
        credentials(installation).open()


def test_secret_wrappers_do_not_log_or_serialize(
    installation: Installation,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import logging

    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    source = credentials(installation)
    with source.open() as secret:
        print(secret, repr(secret), source)
        logging.warning("credential=%s repr=%r", secret, secret)
        with pytest.raises(BoundaryError) as error:
            pickle.dumps(secret)
        assert SENTINEL not in str(error.value)
        assert not hasattr(secret, "__dict__")
    with pytest.raises(BoundaryError):
        secret.view()
    assert SENTINEL not in caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize(
    "mode, uid, expected_uid, private, accepted",
    [
        (0o40755, 0, 0, False, True),
        (0o40750, 0, 0, False, True),
        (0o40775, 0, 0, False, False),
        (0o40755, 1000, 0, False, False),
        (0o40700, 1000, 1000, True, True),
        (0o40750, 1000, 1000, True, False),
        (0o40700, 0, 1000, True, False),
        (0o100600, 1000, 1000, True, False),
    ],
)
def test_service_and_private_directory_policy(
    mode: int,
    uid: int,
    expected_uid: int,
    private: bool,
    accepted: bool,
) -> None:
    from scryntic.configuration.paths import _directory_mode

    info = os.stat_result((mode, 1, 1, 1, uid, 0, 0, 0, 0, 0))
    if accepted:
        _directory_mode(info, expected_uid, private=private)
    else:
        with pytest.raises(BoundaryError):
            _directory_mode(info, expected_uid, private=private)


def test_wrong_credential_owner_is_rejected(
    installation: Installation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stat

    path = installation.credential_dir / "token"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    original_stat = os.fstat

    def foreign_file(fd: int) -> os.stat_result:
        result = original_stat(fd)
        if stat.S_ISREG(result.st_mode):
            fields = list(result)
            fields[4] = result.st_uid + 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(os, "fstat", foreign_file)
    with pytest.raises(BoundaryError):
        credentials(installation).open()


def test_protected_paths_are_not_user_state(installation: Installation) -> None:
    with pytest.raises(BoundaryError):
        validate_directories(dataclasses.replace(installation, state_dir=Path("/etc")))
    with pytest.raises(BoundaryError):
        validate_directories(dataclasses.replace(installation, service_mode=True))
