"""Optional Textual interface over the same typed workflows as the CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    ProgressBar,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from scryntic.application.operations import (
    ApplicationError,
    ApplicationResult,
    ErrorCode,
    Request,
    Services,
    Workflow,
)
from scryntic.workflows.wire import encode_result

_CANCEL_SELECTOR = "#cancel"

if TYPE_CHECKING:
    from scryntic.configuration.paths import Installation
    from scryntic.daemon.logging import StructuredLogger


class Confirmation(ModalScreen[bool]):
    """Require an explicit decision before the shared destructive workflow."""

    DEFAULT_CSS = """
    Confirmation { align: center middle; background: $background 80%; }
    #confirmation { width: 62; height: auto; padding: 2 3;
                    border: round $warning; background: $surface; }
    #confirmation-message { margin-bottom: 2; height: auto; }
    #confirmation-buttons { height: 3; align: right middle; }
    #confirmation-buttons Button { margin-left: 1; }
    """
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmation"):
            yield Static(self.message, markup=False, id="confirmation-message")
            with Horizontal(id="confirmation-buttons"):
                yield Button("Go back", id="confirm-back")
                yield Button("Confirm", variant="warning", id="confirm-run")

    @on(Button.Pressed, "#confirm-back")
    def action_back(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-run")
    def confirm(self) -> None:
        self.dismiss(True)


class Help(ModalScreen[None]):
    DEFAULT_CSS = """
    Help { align: center middle; background: $background 80%; }
    #help { width: 68; height: auto; padding: 2 3;
            border: round $accent; background: $surface; }
    #help Static { height: auto; margin-bottom: 1; }
    """
    BINDINGS = [Binding("escape,?", "back", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static("SCRYNTIC / WORKSPACE HELP", markup=False)
            yield Static(
                "Search the workflow list, then press Enter to open its form.\n"
                "Tab moves between fields. Choices use the arrow keys.\n\n"
                "Ctrl+R / F5    Run the selected workflow\n"
                "Ctrl+K         Cancel and wait for owned work\n"
                "Ctrl+F         Search workflows\n"
                "Ctrl+Q         Settle work and quit\n\n"
                "Results are public service output. Filter the table below.\n"
                "Status is an offline installation snapshot; collector control\n"
                "is outside this workstation interface.",
                markup=False,
            )
            yield Button("Close help", id="help-close", variant="primary")

    @on(Button.Pressed, "#help-close")
    def action_back(self) -> None:
        self.dismiss(None)


class ResultDetail(ModalScreen[None]):
    """Keep table cells compact while preserving the selected public value."""

    DEFAULT_CSS = """
    ResultDetail { align: center middle; background: $background 80%; }
    #detail { width: 85%; max-width: 110; height: 75%; padding: 1 2;
              border: round $accent; background: $surface; }
    #detail Label { text-style: bold; height: 1; margin-bottom: 1; }
    #detail VerticalScroll { height: 1fr; margin-bottom: 1; }
    #detail-value { height: auto; }
    """
    BINDINGS = [Binding("escape", "back", "Close")]

    def __init__(self, key: str, value: str) -> None:
        super().__init__()
        self.key = key
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="detail"):
            yield Label(Text(self.key))
            with VerticalScroll():
                yield Static(self.value, markup=False, id="detail-value")
            yield Button("Close detail", id="detail-close", variant="primary")

    @on(Button.Pressed, "#detail-close")
    def action_back(self) -> None:
        self.dismiss(None)


class ScrynticApp(App[None]):
    """Workstation workspace with injected service ownership for both interfaces."""

    TITLE = "Scryntic"
    SUB_TITLE = "Workstation workspace"
    CSS = """
    Screen { background: #101722; color: #d8e5ed; }
    Header { background: #172534; color: #98d7e4; }
    Footer { background: #172534; }
    #workspace { height: 1fr; }
    #navigation { width: 30; min-width: 24; background: #131e2b;
                  border-right: solid #2a4356; padding: 1; }
    #brand { color: #7ed4d1; text-style: bold; margin-bottom: 1; }
    #workflow-search { margin-bottom: 1; }
    #workflows { height: 1fr; border: none; background: #131e2b; }
    #nav-note { color: #849eaf; height: auto; padding-top: 1; }
    #content { width: 1fr; padding: 1 2; }
    #dashboard { height: 5; background: #172534; padding: 1 2;
                 border-left: thick #5ebbb7; margin-bottom: 1; }
    #dashboard-title { text-style: bold; color: #97e1da; }
    #dashboard-note { color: #a2b7c6; height: auto; }
    #workflow-title { text-style: bold; color: #e8f3fa; height: 1; }
    #workflow-group { color: #829eaf; height: 1; margin-bottom: 1; }
    #form { height: auto; max-height: 15; min-height: 1; }
    .field { height: auto; margin-bottom: 1; }
    .field Label { height: 1; color: #bdd2df; }
    .field Input, .field Select { margin-top: 0; }
    .field-help { color: #849eaf; height: auto; }
    #actions { height: 3; margin-top: 1; }
    #actions Button { margin-right: 1; }
    #progress { height: 1; margin-top: 1; }
    #status { height: auto; min-height: 1; max-height: 3; color: #9cbacb;
              margin-bottom: 1; }
    #status.error { color: #f2a8a8; }
    #result-heading { text-style: bold; color: #97e1da; height: 1; }
    #result-search { height: 3; margin-top: 1; }
    #results { height: 1fr; min-height: 4; background: #131e2b;
               border: round #2a4356; }
    Input { border: tall #2a4356; background: #101722; }
    Input:focus { border: tall #5ebbb7; }
    Button.-primary { background: #23625f; }
    """
    BINDINGS = [
        Binding("ctrl+r,f5", "run", "Run"),
        Binding("r", "run", "Run", show=False),
        Binding("ctrl+k", "cancel", "Cancel"),
        Binding("ctrl+f", "search", "Search"),
        Binding("?", "help", "Help"),
        Binding("ctrl+q,ctrl+c", "quit", "Quit", priority=True),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        services: Services,
        workflows: tuple[Workflow, ...],
        *,
        diagnostics: StructuredLogger | None = None,
    ) -> None:
        super().__init__()
        self.services = services
        self.workflows = workflows
        self._selected: Workflow | None = workflows[0] if workflows else None
        self._execution: asyncio.Task[None] | None = None
        self._cancellation: asyncio.Task[None] | None = None
        self._busy = False
        self._cancelling = False
        self._services_closing = False
        self._services_closed = False
        self._rows: list[tuple[str, str]] = []
        self._visible_rows: list[tuple[str, str]] = []
        self._diagnostics = diagnostics
        self._restore_asyncio_diagnostics: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            with Vertical(id="navigation"):
                yield Label("SCRYNTIC / WORKFLOWS", id="brand")
                yield Input(placeholder="Search workflows…", id="workflow-search")
                yield OptionList(id="workflows")
                yield Static(
                    "WORKSTATION\nExplicit actions · durable checkpoints",
                    markup=False,
                    id="nav-note",
                )
            with VerticalScroll(id="content"):
                with Vertical(id="dashboard"):
                    yield Label("YOUR LOCAL RESEARCH WORKSPACE", id="dashboard-title")
                    yield Static(
                        "Choose a workflow to inspect data or run bounded work. "
                        "No remote connection starts on launch.",
                        markup=False,
                        id="dashboard-note",
                    )
                yield Label("", id="workflow-title", markup=False)
                yield Label("", id="workflow-group", markup=False)
                yield VerticalScroll(id="form")
                with Horizontal(id="actions"):
                    yield Button("Run workflow", variant="primary", id="run")
                    yield Button("Cancel work", id="cancel", disabled=True)
                    yield Button("Help", id="open-help")
                yield ProgressBar(
                    total=1, show_percentage=False, show_eta=False, id="progress"
                )
                yield Static(
                    "Ready. Select a workflow to begin.", markup=False, id="status"
                )
                yield Label("RESULTS / PUBLIC OUTPUT", id="result-heading")
                yield Input(placeholder="Filter result rows…", id="result-search")
                yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Footer()

    async def on_mount(self) -> None:
        if self._diagnostics is not None:
            self._restore_asyncio_diagnostics = (
                self._diagnostics.install_asyncio_handler(asyncio.get_running_loop())
            )
        self.query_one("#results", DataTable).add_columns("Field / record", "Value")
        self._filter_workflows("")
        await self._show_workflow()

    def _filter_workflows(self, query: str) -> None:
        navigation = self.query_one("#workflows", OptionList)
        navigation.clear_options()
        normalized = query.casefold().strip()
        navigation.add_options(
            Option(Text(f"{item.group}\n{item.title}"), id=item.operation.value)
            for item in self.workflows
            if normalized
            in f"{item.group} {item.title} {item.operation.value}".casefold()
        )
        navigation.highlighted = 0 if navigation.option_count else None

    @on(Input.Changed, "#workflow-search")
    def workflow_search(self, event: Input.Changed) -> None:
        self._filter_workflows(event.value)

    @on(OptionList.OptionSelected, "#workflows")
    async def workflow_selected(self, event: OptionList.OptionSelected) -> None:
        if self._busy:
            self._status(
                "Wait for the current operation to settle before changing forms."
            )
            return
        self._selected = next(
            (
                item
                for item in self.workflows
                if item.operation.value == event.option_id
            ),
            None,
        )
        await self._show_workflow()

    async def _show_workflow(self) -> None:
        form = self.query_one("#form", VerticalScroll)
        await form.remove_children()
        selected = self._selected
        self.query_one("#run", Button).disabled = selected is None
        if selected is None:
            self._status("No workflows are available for this installation.")
            return
        self.query_one("#workflow-title", Label).update(Text(selected.title))
        self.query_one("#workflow-group", Label).update(
            Text(f"{selected.group}  /  {selected.operation.value}")
        )
        if not selected.fields:
            await form.mount(Static("No parameters required.", markup=False))
        for field in selected.fields:
            control_id = "field-" + field.name.replace("_", "-")
            if field.choices:
                control: Input | Select[str] = Select(
                    [(Text(value), value) for value in field.choices],
                    value=field.default
                    if field.default in field.choices
                    else field.choices[0],
                    allow_blank=False,
                    id=control_id,
                )
            else:
                control = Input(value=field.default, id=control_id)
            widgets: list[Widget] = [
                Label(Text(field.label + (" *" if field.required else ""))),
                control,
            ]
            if field.help:
                widgets.append(Static(field.help, markup=False, classes="field-help"))
            await form.mount(Vertical(*widgets, classes="field"))

    def _parameters(self) -> dict[str, str]:
        assert self._selected is not None
        values: dict[str, str] = {}
        for field in self._selected.fields:
            control = self.query_one("#field-" + field.name.replace("_", "-"))
            if isinstance(control, Input):
                values[field.name] = control.value
            elif isinstance(control, Select):
                value = control.value
                values[field.name] = value if isinstance(value, str) else ""
        return values

    @on(Button.Pressed, "#run")
    def action_run(self) -> None:
        if self._busy or self._services_closing or self._selected is None:
            return
        try:
            request = self._selected.request(self._parameters())
        except ApplicationError as error:
            self._status(str(error), error=True)
            return
        if self._selected.confirmation:
            self.push_screen(
                Confirmation(self._selected.confirmation),
                lambda confirmed: self._start(request) if confirmed else None,
            )
        else:
            self._start(request)

    def _start(self, request: Request) -> None:
        if self._busy or self._services_closing:
            return
        self._set_busy(True)
        self._rows = []
        self._filter_results(self.query_one("#result-search", Input).value)
        self._status("Running bounded work. Durable progress is owned by the service.")
        self._execution = asyncio.create_task(self._execute(request))

    async def _execute(self, request: Request) -> None:
        try:
            result = await self.services.execute(request)
            if not self._services_closing:
                self._render_result(result)
                self._status(
                    "Complete. Public result is ready."
                    if result.complete
                    else "Incomplete. Inspect durable state before continuing."
                )
        except ApplicationError as error:
            if not self._services_closing:
                self._status(str(error), error=True)
        except Exception:
            if not self._services_closing:
                self._status(str(ApplicationError(ErrorCode.FAILED)), error=True)
        finally:
            if not self._services_closing and not self._cancelling:
                self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one("#run", Button).disabled = busy or self._selected is None
        self.query_one(_CANCEL_SELECTOR, Button).disabled = not busy
        for control in self.query("#form Input, #form Select"):
            control.disabled = busy
        self.query_one("#progress", ProgressBar).update(
            total=None if busy else 1, progress=0
        )

    def _status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.set_class(error, "error")
        status.update(Text(message))

    def _render_result(self, result: ApplicationResult) -> None:
        encode_result(result)
        data: dict[str, object] = json.loads(result.payload)
        self._rows = []
        for key, value in data.items():
            if isinstance(value, list):
                for index, item in enumerate(value[:200]):
                    self._rows.append((f"{key}[{index}]", self._display_value(item)))
                    if len(self._rows) >= 200:
                        break
            else:
                self._rows.append((key, self._display_value(value)))
            if len(self._rows) >= 200:
                break
        self.query_one("#result-heading", Label).update(
            Text(f"RESULTS / {result.operation.value} · up to 200 rows")
        )
        self._filter_results(self.query_one("#result-search", Input).value)

    @staticmethod
    def _display_value(value: object) -> str:
        return value if isinstance(value, str) else json.dumps(value, sort_keys=True)

    @on(Input.Changed, "#result-search")
    def result_search(self, event: Input.Changed) -> None:
        self._filter_results(event.value)

    def _filter_results(self, query: str) -> None:
        table = self.query_one("#results", DataTable)
        table.clear()
        normalized = query.casefold().strip()
        self._visible_rows = []
        for key, value in self._rows:
            if normalized in f"{key} {value}".casefold():
                self._visible_rows.append((key, value))
                table.add_row(Text(key), Text(value[:2048]))

    @on(DataTable.RowSelected, "#results")
    def result_selected(self, event: DataTable.RowSelected) -> None:
        index = event.data_table.get_row_index(event.row_key)
        if 0 <= index < len(self._visible_rows):
            self.push_screen(ResultDetail(*self._visible_rows[index]))

    @on(Button.Pressed, _CANCEL_SELECTOR)
    def action_cancel(self) -> None:
        if not self._busy or self._cancelling:
            return
        self._cancelling = True
        self.query_one(_CANCEL_SELECTOR, Button).disabled = True
        self._status("Cancellation requested. Waiting for owned work to settle…")
        self._cancellation = asyncio.create_task(self._cancel_owned())

    async def _cancel_owned(self) -> None:
        try:
            await self.services.cancel()
            if self._execution is not None:
                await asyncio.shield(self._execution)
        except ApplicationError as error:
            if not self._services_closing:
                self._status(str(error), error=True)
        except Exception:
            if not self._services_closing:
                self._status(str(ApplicationError(ErrorCode.FAILED)), error=True)
        finally:
            self._cancelling = False
            if not self._services_closing:
                if self._execution is not None and self._execution.done():
                    self._set_busy(False)
                elif self._busy:
                    self.query_one(_CANCEL_SELECTOR, Button).disabled = False

    def action_search(self) -> None:
        self.query_one("#workflow-search", Input).focus()

    @on(Button.Pressed, "#open-help")
    def action_help(self) -> None:
        self.push_screen(Help())

    async def _close_services(self) -> None:
        if self._services_closed:
            return
        self._services_closing = True
        try:
            if self._cancellation is not None and not self._cancellation.done():
                await asyncio.shield(self._cancellation)
            if self._execution is not None and not self._execution.done():
                await self.services.cancel()
                await asyncio.shield(self._execution)
            await self.services.aclose()
        finally:
            self._services_closed = True

    async def action_quit(self) -> None:
        try:
            await self._close_services()
        except Exception:
            self._services_closing = False
            self._services_closed = False
            self._status(str(ApplicationError(ErrorCode.FAILED)), error=True)
            return
        self.exit()

    async def on_unmount(self) -> None:
        try:
            await self._close_services()
        except Exception:
            # Shutdown errors must not expose adapter exception text or locals.
            self.exit(return_code=6)
        finally:
            if self._restore_asyncio_diagnostics is not None:
                self._restore_asyncio_diagnostics()
                self._restore_asyncio_diagnostics = None


def run_tui(installation: Installation | None = None) -> int:
    """Compose optional UI dependencies only after an explicit TUI invocation."""
    from scryntic.presentation import diagnostics_boundary
    from scryntic.use_cases import ApplicationServices, available_workflows

    with diagnostics_boundary() as diagnostics:
        app = ScrynticApp(
            ApplicationServices(installation),
            available_workflows(),
            diagnostics=diagnostics,
        )
        app.run()
        return app.return_code or 0
