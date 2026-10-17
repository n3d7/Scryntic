"""Installed argparse interface over reusable typed application services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import stat
import sys
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

from scryntic.application.operations import (
    ApplicationError,
    ErrorCode,
    Request,
    Workflow,
)
from scryntic.configuration.paths import Installation, directory
from scryntic.configuration.values import BoundaryError
from scryntic.daemon.logging import StructuredLogger
from scryntic.presentation import diagnostics_boundary
from scryntic.use_cases import ApplicationServices, available_workflows
from scryntic.workflows.wire import encode_error, encode_result

if TYPE_CHECKING:
    from scryntic.composition import SliceResult


def run_fake_slice(
    installation: Installation,
    *,
    epoch: str,
    code_revision: str,
    dependency_lock_sha256: str,
    provider_name: Literal["persistence", "trend"],
    horizon: int,
) -> SliceResult:
    """Load the optional F09 composition only for its explicit legacy command."""
    from scryntic.composition import run_fake_slice as compose

    return compose(
        installation,
        epoch=epoch,
        code_revision=code_revision,
        dependency_lock_sha256=dependency_lock_sha256,
        provider_name=provider_name,
        horizon=horizon,
    )


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ApplicationError(ErrorCode.INVALID)


class LegacyOption(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        namespace.legacy_used = True


def _parser() -> Parser:
    parser = Parser(
        description="Scryntic local application workflows and optional terminal UI",
        allow_abbrev=False,
    )
    parser.set_defaults(legacy_used=False)
    parser.add_argument(
        "--home",
        type=Path,
        help="Workstation home; approved private roots must already exist",
    )
    parser.add_argument(
        "--installation", choices=("workstation", "service"), default="workstation"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Version 1 bounded JSON (default for application workflows)",
    )
    parser.add_argument("--epoch", action=LegacyOption, help=argparse.SUPPRESS)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("uv.lock"),
        action=LegacyOption,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--code-revision",
        default="f09-fake-v1",
        action=LegacyOption,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--provider",
        choices=("persistence", "trend"),
        default="persistence",
        action=LegacyOption,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--horizon", type=int, default=2, action=LegacyOption, help=argparse.SUPPRESS
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "tui", help="Open the optional interactive terminal UI", allow_abbrev=False
    )
    groups: dict[str, argparse._SubParsersAction[Parser]] = {}
    for workflow in available_workflows():
        names = workflow.operation.value.split(".")
        if len(names) == 2:
            if names[0] not in groups:
                group = commands.add_parser(
                    names[0], help=workflow.group, allow_abbrev=False
                )
                groups[names[0]] = group.add_subparsers(
                    dest="subcommand", required=True
                )
            command = groups[names[0]].add_parser(
                names[1], help=workflow.title, allow_abbrev=False
            )
        else:
            command = commands.add_parser(
                names[0], help=workflow.title, allow_abbrev=False
            )
        command.set_defaults(workflow=workflow)
        command.add_argument(
            "--json", action="store_true", help="Version 1 bounded JSON"
        )
        for field in workflow.fields:
            command.add_argument(
                "--" + field.name.replace("_", "-"),
                dest=field.name,
                default=field.default,
                required=field.required,
                choices=field.choices or None,
                help=field.help or field.label,
            )
    return parser


def _installation(args: argparse.Namespace) -> Installation:
    if args.installation == "service":
        if args.home is not None:
            raise ApplicationError(ErrorCode.INVALID)
        return Installation.service(os.geteuid())
    return Installation.workstation(
        home=args.home, environment={} if args.home is not None else None
    )


def _legacy_lock(path: Path) -> bytes:
    if ".." in path.parts:
        raise ApplicationError(ErrorCode.INVALID)
    with directory(path.absolute().parent, os.geteuid(), private=False) as root:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=root,
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or before.st_mode & 0o7022
                or before.st_size > 1024 * 1024
            ):
                raise ApplicationError(ErrorCode.CONFIGURATION)
            data = os.read(fd, 1024 * 1024 + 1)
            after = os.fstat(fd)
            if len(data) != before.st_size or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ApplicationError(ErrorCode.CONFIGURATION)
            return data
        finally:
            os.close(fd)


def _legacy(args: argparse.Namespace) -> int:
    if args.home is None or args.epoch is None:
        raise ApplicationError(ErrorCode.INVALID)
    value = run_fake_slice(
        _installation(args),
        epoch=args.epoch,
        code_revision=args.code_revision,
        dependency_lock_sha256=sha256(_legacy_lock(args.lock_file)).hexdigest(),
        provider_name=args.provider,
        horizon=args.horizon,
    )
    print(
        json.dumps(
            {
                "dataset_manifest_sha256": value.dataset.manifest_sha256,
                "dataset_rows": value.dataset.row_count,
                "forecast_artifact_sha256": value.forecast.sha256,
                "provider_id": value.forecast.provider_id,
                "model_revision": value.forecast.model_revision,
            },
            sort_keys=True,
        )
    )
    return 0


async def _run(
    request: Request, installation: Installation, logger: StructuredLogger
) -> int:
    services = ApplicationServices(installation)
    loop = asyncio.get_running_loop()
    restore_asyncio = logger.install_asyncio_handler(loop)
    signal_task: asyncio.Task[None] | None = None
    signalled = False

    def stop() -> None:
        nonlocal signalled, signal_task
        signalled = True
        if signal_task is None:
            signal_task = asyncio.create_task(services.cancel())
            signal_task.add_done_callback(_observe_stop)

    def _observe_stop(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        value = await services.execute(request)
        if signalled:
            raise ApplicationError(ErrorCode.CANCELLED)
        print(encode_result(value).decode())
        return 0 if value.complete else 7
    finally:
        await services.aclose()
        if signal_task is not None:
            await asyncio.wait((signal_task,))
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        restore_asyncio()


def main(argv: Sequence[str] | None = None) -> int:
    with diagnostics_boundary() as logger:
        return _main(argv, logger)


def _main(argv: Sequence[str] | None, logger: StructuredLogger) -> int:
    operation = None
    try:
        values = list(sys.argv[1:] if argv is None else argv)
        if (
            len(values) > 4096
            or any(type(value) is not str or "\x00" in value for value in values)
            or sum(len(value.encode()) for value in values) > 65536
        ):
            raise ApplicationError(ErrorCode.INVALID)
        parser = _parser()
        if not values:
            parser.print_help()
            return 0
        args = parser.parse_args(values)
        if args.command is not None and args.legacy_used:
            raise ApplicationError(ErrorCode.INVALID)
        if args.command == "tui":
            from scryntic.tui import run_tui

            return run_tui(_installation(args))
        workflow: Workflow | None = getattr(args, "workflow", None)
        if workflow is None:
            return _legacy(args)
        operation = workflow.operation
        request = workflow.request(
            {field.name: getattr(args, field.name) for field in workflow.fields}
        )
        return asyncio.run(_run(request, _installation(args), logger))
    except ApplicationError as caught:
        print(encode_error(caught, operation).decode(), file=sys.stderr)
        return caught.exit_code
    except BoundaryError:
        error = ApplicationError(ErrorCode.CONFIGURATION)
    except ImportError:
        error = ApplicationError(ErrorCode.UNAVAILABLE)
    except (asyncio.CancelledError, KeyboardInterrupt):
        error = ApplicationError(ErrorCode.CANCELLED)
    except BrokenPipeError:
        return 141
    except Exception:
        error = ApplicationError(ErrorCode.FAILED)
    print(encode_error(error, operation).decode(), file=sys.stderr)
    return error.exit_code
