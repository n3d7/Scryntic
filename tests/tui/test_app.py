"""Pilot tests for workflow navigation, public rendering and owned task lifetime."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import pytest

from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Field,
    Operation,
    Request,
    Workflow,
)
from scryntic.workflows.wire import result

pytest.importorskip("textual")
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Input, OptionList, Static


def test_generated_workflows_with_real_application_services(tmp_path: Path) -> None:
    from scripts.verify_cli_install import _installation, _verify_tui

    home = tmp_path / "workstation"
    home.mkdir(mode=0o700)
    _installation(home)
    asyncio.run(_verify_tui(home))


@dataclass(frozen=True)
class InspectRequest:
    identifier: str
    operation: Operation = Operation.DATASET_INSPECT


def workflows() -> tuple[Workflow, ...]:
    return (
        Workflow(
            Operation.DATASET_INSPECT,
            "Inspect dataset",
            "Datasets",
            (Field("identifier", "Dataset identifier", required=True),),
            lambda values: InspectRequest(values["identifier"]),
        ),
        Workflow(
            Operation.JOBS_CANCEL,
            "Cancel job",
            "Jobs",
            (Field("identifier", "Job identifier", required=True),),
            lambda values: InspectRequest(values["identifier"], Operation.JOBS_CANCEL),
            confirmation="Cancel this durable job?",
        ),
    )


class Services:
    def __init__(self, *, delayed: bool = False, fail: bool = False) -> None:
        self.delayed = delayed
        self.fail = fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = False
        self.closed_after_settle = False

    async def execute(self, request: Request) -> ApplicationResult:
        assert isinstance(request, InspectRequest)
        self.started.set()
        if self.delayed:
            await self.release.wait()
        self.settled = True
        if self.fail:
            raise RuntimeError("PRIVATE-ERROR-SENTINEL [bold]unsafe[/bold]")
        return result(
            request.operation,
            {"identifier": request.identifier, "state": "[bold]public literal[/bold]"},
        )

    async def cancel(self) -> None:
        self.release.set()

    async def aclose(self) -> None:
        self.closed_after_settle = self.settled or not self.started.is_set()


def test_form_validation_and_public_table_rendering() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = Services()
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#run")
            await pilot.pause()
            assert not services.started.is_set()
            assert "Invalid" in str(app.query_one("#status", Static).render())
            await pilot.pause(0.25)
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.click("#run")
            await pilot.pause()
            table = app.query_one("#results", DataTable)
            assert table.row_count == 2
            assert str(table.get_cell_at(Coordinate(0, 1))) == "dataset-17"
            assert (
                str(table.get_cell_at(Coordinate(1, 1)))
                == "[bold]public literal[/bold]"
            )
            app.query_one("#result-search", Input).value = "dataset-17"
            await pilot.pause()
            assert table.row_count == 1

    asyncio.run(scenario())


def test_search_navigation_and_confirmation() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = Services()
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#workflow-search", Input).value = "cancel"
            await pilot.pause()
            navigation = app.query_one("#workflows", OptionList)
            assert navigation.option_count == 1
            navigation.focus()
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#field-identifier", Input).value = "job-17"
            await pilot.click("#run")
            await pilot.pause()
            assert not services.started.is_set()
            await pilot.click("#confirm-back")
            await pilot.pause()
            assert not services.started.is_set()
            await pilot.click("#run")
            await pilot.pause()
            await pilot.click("#confirm-run")
            await pilot.pause()
            assert app.query_one("#results", DataTable).row_count == 2

    asyncio.run(scenario())


def test_busy_cancellation_keeps_ui_responsive_and_settles_work() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = Services(delayed=True)
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.click("#run")
            await asyncio.wait_for(services.started.wait(), 2)
            await pilot.pause()
            assert app.query_one("#run", Button).disabled
            assert not app.query_one("#cancel", Button).disabled
            app.query_one("#workflow-search", Input).value = "job"
            await pilot.pause()
            assert app.query_one("#workflows", OptionList).option_count == 1
            await pilot.click("#cancel")
            await pilot.pause()
            assert not app.query_one("#run", Button).disabled
            assert services.settled
            assert app.query_one("#results", DataTable).row_count == 2
            await app.action_quit()
        assert services.closed_after_settle

    asyncio.run(scenario())


def test_quit_waits_for_owned_execution_before_closing() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = Services(delayed=True)
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.click("#run")
            await asyncio.wait_for(services.started.wait(), 2)
            await app.action_quit()
        assert services.closed_after_settle

    asyncio.run(scenario())


def test_unexpected_service_error_never_reaches_rendered_output() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        app = ScrynticApp(Services(fail=True), workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.click("#run")
            await pilot.pause()
            assert str(ApplicationError(ErrorCode.FAILED)) in str(
                app.query_one("#status", Static).render()
            )
            assert "PRIVATE-ERROR-SENTINEL" not in app.export_screenshot()
            assert app.query_one("#results", DataTable).row_count == 0

    asyncio.run(scenario())


def test_keyboard_choices_and_result_details() -> None:
    async def scenario() -> None:
        from textual.widgets import Select

        from scryntic.tui import ScrynticApp

        options = (
            Workflow(
                Operation.DATASET_INSPECT,
                "Inspect dataset",
                "Datasets",
                (
                    Field(
                        "identifier",
                        "Dataset",
                        "dataset-17",
                        choices=("dataset-17", "dataset-18"),
                    ),
                ),
                lambda values: InspectRequest(values["identifier"]),
            ),
        )
        app = ScrynticApp(Services(), options)
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Select).value = "dataset-18"
            await pilot.press("ctrl+r")
            await pilot.pause()
            table = app.query_one("#results", DataTable)
            assert str(table.get_cell_at(Coordinate(0, 1))) == "dataset-18"
            table.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "dataset-18" in str(
                app.screen.query_one("#detail-value", Static).render()
            )
            await pilot.press("escape")
            await pilot.press("?")
            await pilot.pause()
            assert app.screen.query_one("#help-close", Button)
            await pilot.press("escape")
            await pilot.press("ctrl+f")
            await pilot.pause()
            assert app.focused is app.query_one("#workflow-search", Input)

    asyncio.run(scenario())


def test_result_table_is_bounded_and_incomplete_state_is_truthful() -> None:
    class ManyRecords(Services):
        async def execute(self, request: Request) -> ApplicationResult:
            return result(
                request.operation, {"records": list(range(300))}, complete=False
            )

    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        app = ScrynticApp(ManyRecords(), workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert app.query_one("#results", DataTable).row_count == 200
            assert "Incomplete" in str(app.query_one("#status", Static).render())

    asyncio.run(scenario())


def test_compact_terminal_can_scroll_to_results() -> None:
    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        app = ScrynticApp(Services(), workflows())
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.press("ctrl+r")
            await pilot.pause()
            table = app.query_one("#results", DataTable)
            table.focus()
            await pilot.pause()
            assert 1 <= table.region.y < 23
            assert table.row_count == 2

    asyncio.run(scenario())


def test_cancellation_wait_does_not_block_navigation_messages() -> None:
    class SlowCancellation(Services):
        def __init__(self) -> None:
            super().__init__(delayed=True)
            self.cancel_started = asyncio.Event()
            self.cancel_release = asyncio.Event()

        async def cancel(self) -> None:
            self.cancel_started.set()
            await self.cancel_release.wait()
            self.release.set()

    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = SlowCancellation()
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.press("ctrl+r")
            await asyncio.wait_for(services.started.wait(), 2)
            click = asyncio.create_task(pilot.click("#cancel"))
            await asyncio.wait_for(services.cancel_started.wait(), 2)
            try:
                app.query_one("#workflow-search", Input).value = "job"
                for _ in range(5):
                    await asyncio.sleep(0.01)
                assert app.query_one("#workflows", OptionList).option_count == 1
                assert not services.settled
            finally:
                services.cancel_release.set()
                await click
                await pilot.pause()
            assert services.settled

    asyncio.run(scenario())


def test_run_stays_disabled_until_cancellation_cleanup_settles() -> None:
    class CleanupCancellation(Services):
        def __init__(self) -> None:
            super().__init__(delayed=True)
            self.cleanup_started = asyncio.Event()
            self.cleanup_release = asyncio.Event()

        async def cancel(self) -> None:
            self.release.set()
            self.cleanup_started.set()
            await self.cleanup_release.wait()

    async def scenario() -> None:
        from scryntic.tui import ScrynticApp

        services = CleanupCancellation()
        app = ScrynticApp(services, workflows())
        async with app.run_test(size=(120, 48)) as pilot:
            app.query_one("#field-identifier", Input).value = "dataset-17"
            await pilot.press("ctrl+r")
            await asyncio.wait_for(services.started.wait(), 2)
            click = asyncio.create_task(pilot.click("#cancel"))
            await asyncio.wait_for(services.cleanup_started.wait(), 2)
            try:
                for _ in range(5):
                    await asyncio.sleep(0.01)
                assert services.settled
                assert app.query_one("#run", Button).disabled
            finally:
                services.cleanup_release.set()
                await click
                await pilot.pause()
            assert not app.query_one("#run", Button).disabled

    asyncio.run(scenario())


def test_standalone_asyncio_diagnostics_are_fixed_and_handler_is_restored() -> None:
    from scryntic.daemon.logging import StructuredLogger
    from scryntic.tui import ScrynticApp

    async def scenario() -> None:
        output = io.StringIO()
        logger = StructuredLogger(sink=output)
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        app = ScrynticApp(Services(), workflows(), diagnostics=logger)
        async with app.run_test():
            loop.call_exception_handler(
                {"message": "TUI-SENTINEL", "exception": RuntimeError("TUI-SENTINEL")}
            )
            await app.action_quit()
        logger.close(timeout=1)
        assert loop.get_exception_handler() is previous
        assert "TUI-SENTINEL" not in output.getvalue()
        assert '"event":"pipeline_fault"' in output.getvalue()

    asyncio.run(scenario())
