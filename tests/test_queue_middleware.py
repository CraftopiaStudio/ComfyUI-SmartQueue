import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.autopilot_state import AutopilotState
from backend.autopilot import Decision
from backend.queue_middleware import create_queue_middleware


async def _make_app(state: AutopilotState, enabled: bool = True):
    middleware = create_queue_middleware(state, is_enabled=lambda: enabled)
    app = web.Application(middlewares=[middleware])

    async def handle_prompt(request):
        return web.json_response({"ok": True})

    app.router.add_post("/prompt", handle_prompt)
    return app


class TestQueueMiddlewarePassthrough(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        return await _make_app(self.state, enabled=True)

    @unittest_run_loop
    async def test_passes_through_when_not_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 200


class TestQueueMiddlewareBlocking(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        return await _make_app(self.state, enabled=True)

    @unittest_run_loop
    async def test_blocks_with_423_when_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 423
        body = await resp.json()
        assert "GPU too hot" in body["error"]


class TestQueueMiddlewareManualPause(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        self.state.set_manual_pause(True)
        return await _make_app(self.state, enabled=True)

    @unittest_run_loop
    async def test_blocks_with_423_when_manually_paused_even_without_autopilot_decision(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 423
        body = await resp.json()
        assert "Manually paused" in body["error"]


class TestQueueMiddlewareDisabled(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        return await _make_app(self.state, enabled=False)

    @unittest_run_loop
    async def test_passes_through_when_master_toggle_disabled_even_if_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 200
