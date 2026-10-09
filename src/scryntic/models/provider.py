"""Common local provider boundary; F20 owns admission, jobs and acceptance."""

from typing import Literal, Protocol, cast

from scryntic.jobs.admission import ModelReview
from scryntic.jobs.contracts import JobAttempt, JobRequest
from scryntic.model_worker.launcher import CPUWorker, IsolatedFakeProvider
from scryntic.models.definitions import ModelDefinition, definition
from scryntic.models.inventory import RuntimeBundle
from scryntic.models.selection import ModelSelection
from scryntic.models.window import ForecastWindow


class WindowSource(Protocol):
    def window(self, job: JobRequest) -> ForecastWindow: ...


class LocalModelProvider:
    def __init__(
        self, model: ModelDefinition, bundle: RuntimeBundle, windows: WindowSource
    ) -> None:
        if model.adapter is None:
            raise ValueError("Model definition has no local inference adapter")
        self.review: ModelReview = model.review(bundle.inventory_sha256)
        self.worker = CPUWorker(bundle=bundle, model_id=model.name)
        self._windows = windows

    async def execute(self, attempt: JobAttempt) -> bytes:
        if attempt.job.review != self.review:
            raise ValueError("Selected model review changed")
        window = self._windows.window(attempt.job)
        window.validate(attempt.job)
        return await self.worker.execute_window(attempt, window)


def select_provider(
    selection: ModelSelection, windows: WindowSource
) -> LocalModelProvider | IsolatedFakeProvider:
    model = definition(selection.selected)
    if model.adapter is None:
        return IsolatedFakeProvider(
            model.review(),
            algorithm=cast(Literal["persistence", "trend"], model.algorithm),
        )
    if model.assets is None:
        raise ValueError("Missing registered model asset metadata")
    if (
        selection.artifacts is None
        or selection.runtime is None
        or selection.runtime_sha256 is None
    ):
        raise ValueError("Model provisioning is unavailable")
    bundle = RuntimeBundle.read(
        selection.artifacts, selection.runtime, selection.runtime_sha256, model.assets
    )
    return LocalModelProvider(model, bundle, windows)
