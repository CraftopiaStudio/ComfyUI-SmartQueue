from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.persistence import init_db, add_queue_item
from backend.autopilot_state import AutopilotState
from backend.autopilot import AutopilotSettings, Decision
from backend.routes import register_routes


class TestSmartQueueRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.conn = init_db(":memory:")
        self.state = AutopilotState()
        self.settings = AutopilotSettings()
        app = web.Application()
        register_routes(app, self.conn, self.state, self.settings)
        return app

    @unittest_run_loop
    async def test_status_reports_not_paused_initially(self):
        resp = await self.client.get("/smart_queue/status")
        body = await resp.json()
        assert body["is_paused"] is False

    @unittest_run_loop
    async def test_status_reports_paused_with_reasons(self):
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        resp = await self.client.get("/smart_queue/status")
        body = await resp.json()
        assert body["is_paused"] is True
        assert "GPU too hot" in body["reasons"]

    @unittest_run_loop
    async def test_queue_lists_items_in_order(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert [item["prompt_id"] for item in body["items"]] == ["a", "b"]

    @unittest_run_loop
    async def test_reorder_updates_queue_order(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        resp = await self.client.post("/smart_queue/reorder", json={"ordered_prompt_ids": ["b", "a"]})
        assert resp.status == 200
        resp2 = await self.client.get("/smart_queue/queue")
        body = await resp2.json()
        assert [item["prompt_id"] for item in body["items"]] == ["b", "a"]

    @unittest_run_loop
    async def test_history_is_empty_initially(self):
        resp = await self.client.get("/smart_queue/history")
        body = await resp.json()
        assert body["items"] == []

    @unittest_run_loop
    async def test_get_settings_returns_current_values(self):
        resp = await self.client.get("/smart_queue/settings")
        body = await resp.json()
        assert body["master_enabled"] is True
        assert body["pause_temp_c"] == 80.0

    @unittest_run_loop
    async def test_post_settings_updates_shared_settings_object(self):
        resp = await self.client.post("/smart_queue/settings", json={"pause_temp_c": 90.0, "master_enabled": False})
        assert resp.status == 200
        assert self.settings.pause_temp_c == 90.0
        assert self.settings.master_enabled is False
