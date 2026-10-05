"""Real bounded loopback HTTP complements the identical local job contract."""

import asyncio
from pathlib import Path
from typing import Literal

import pytest
from aiohttp import web

from scryntic.forecast.artifact import ImmutableForecastStore
from tests.jobs.helpers import NOW, job, policy, review
from tests.jobs.test_execution import FixedInputs
from tests.normalization.helpers import installation


@pytest.mark.parametrize("execution", ["local", "remote"])
@pytest.mark.parametrize("algorithm", ["persistence", "trend"])
def test_local_and_remote_share_success_and_idempotency_contract(
    tmp_path: Path,
    execution: Literal["local", "remote"],
    algorithm: Literal["persistence", "trend"],
) -> None:
    from scryntic.jobs.fake import LocalFakeProvider
    from scryntic.jobs.http import HttpFakeProvider, HttpTestDouble
    from scryntic.jobs.service import JobService
    from scryntic.jobs.store import JobStore

    async def exercise() -> None:
        model = review(execution, algorithm=algorithm)
        async with HttpTestDouble(
            review("remote", algorithm=algorithm),
            clock=lambda: NOW,
            algorithm=algorithm,
        ) as double:
            provider = (
                LocalFakeProvider(model, algorithm=algorithm)
                if execution == "local"
                else HttpFakeProvider(model, double.port, algorithm=algorithm)
            )
            target = installation(tmp_path)
            with JobStore(target, clock=lambda: NOW) as store:
                service = JobService(
                    store,
                    (provider,),
                    policy(model, remote=execution == "remote"),
                    ImmutableForecastStore(target),
                    FixedInputs(),
                )
                value = job(
                    execution=execution,
                    remote=execution == "remote",
                    algorithm=algorithm,
                )
                service.submit_job(value)
                first = await service.run(value.job_id)
                second = await service.run(value.job_id)
                assert first == second
                assert first.state.value == "succeeded"
                assert service.result(value.job_id).dataset == value.forecast.dataset
                assert double.executions == (1 if execution == "remote" else 0)
                await service.aclose()

    asyncio.run(exercise())


def test_remote_explicit_resume_reuses_computation_but_fences_response(
    tmp_path: Path,
) -> None:
    from scryntic.jobs.contracts import JobState
    from scryntic.jobs.http import HttpFakeProvider, HttpTestDouble
    from scryntic.jobs.store import JobStore

    async def exercise() -> None:
        model = review("remote")
        value = job(execution="remote", remote=True)
        target = installation(tmp_path)
        async with HttpTestDouble(model, clock=lambda: NOW) as double:
            provider = HttpFakeProvider(model, double.port)
            with JobStore(target, clock=lambda: NOW) as store:
                store.submit(value)
                old = store.claim(value.job_id)
                stale = await provider.execute(old.execution())
                assert store.finish(old, JobState.INTERRUPTED, reason="shutdown")
                current = store.claim(value.job_id, resume=True)
                fresh = await provider.execute(current.execution())
                from scryntic.jobs.codec import decode_response

                execution = current.execution()
                with pytest.raises(ValueError, match="replayed"):
                    decode_response(stale, execution)
                assert (
                    decode_response(fresh, current.execution()).dataset
                    == value.forecast.dataset
                )
                assert double.executions == 1
                assert not store.finish(old, JobState.FAILED, reason="failed")

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "defect", ["redirect", "oversized", "chunked", "compressed", "malformed", "slow"]
)
def test_hostile_http_never_registers_success_or_an_artifact(
    tmp_path: Path, defect: str
) -> None:
    from scryntic.jobs.http import HttpFakeProvider
    from scryntic.jobs.service import JobService
    from scryntic.jobs.store import JobStore

    async def handler(request: web.Request) -> web.StreamResponse:
        await request.read()
        if defect == "redirect":
            return web.Response(
                status=302, headers={"Location": "http://192.0.2.1/never-follow"}
            )
        if defect == "oversized":
            return web.Response(body=b"x" * 65_537, content_type="application/json")
        if defect == "compressed":
            return web.Response(
                body=b"gzip-bomb-sentinel",
                headers={"Content-Encoding": "gzip"},
                content_type="application/json",
            )
        if defect == "chunked":
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            try:
                for _ in range(17):
                    await response.write(b"x" * 4096)
            except ConnectionResetError:
                pass
            return response
        if defect == "slow":
            await asyncio.sleep(0.3)
        return web.Response(body=b'{"malformed":true}', content_type="application/json")

    async def exercise() -> None:
        application = web.Application()
        application.router.add_post("/v1/forecast", handler)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            port = runner.addresses[0][1]
            model = review("remote")
            provider = HttpFakeProvider(model, port, timeout_seconds=0.05)
            target = installation(tmp_path)
            artifacts = ImmutableForecastStore(target)
            with JobStore(target, clock=lambda: NOW) as store:
                service = JobService(
                    store,
                    (provider,),
                    policy(model, remote=True),
                    artifacts,
                    FixedInputs(),
                )
                value = job(execution="remote", remote=True)
                service.submit_job(value)
                result = await asyncio.wait_for(service.run(value.job_id), 1)
                assert result.state.value == "failed"
                assert result.artifact is None
                assert not (target.state_dir / "forecasts").exists()
                await service.aclose()
        finally:
            await runner.cleanup()

    asyncio.run(exercise())
