from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.persistence import init_db, add_queue_item, set_queue_item_status
from backend.autopilot_state import AutopilotState
from backend.autopilot import AutopilotSettings, Decision
from backend.continue_registry import InterruptProcessingException, wait_for_continue
from backend.persistence import list_queue_items, load_held_items
from backend.queue_hold import QueueHold
from backend.routes import register_routes


class FakePromptQueue:
    def __init__(self, queue=None, running=None):
        self.queue = list(queue or [])
        self.running = list(running or [])
        self.put_calls = []

    def get_current_queue_volatile(self):
        return (list(self.running), list(self.queue))

    def delete_queue_item(self, function):
        for i, item in enumerate(self.queue):
            if function(item):
                self.queue.pop(i)
                return True
        return False

    def put(self, item):
        self.put_calls.append(item)
        self.queue.append(item)


class TestSmartQueueRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.conn = init_db(":memory:")
        self.state = AutopilotState()
        self.settings = AutopilotSettings()
        self.queue_hold = QueueHold()
        self.prompt_queue = FakePromptQueue()
        app = web.Application()
        register_routes(
            app, self.conn, self.state, self.settings,
            queue_hold=self.queue_hold, prompt_queue=self.prompt_queue,
        )
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
    async def test_queue_marks_currently_running_item_without_persisting_it(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        self.prompt_queue.running = [(0, "a", {}, {}, [], {})]

        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        by_id = {item["prompt_id"]: item["status"] for item in body["items"]}
        assert by_id == {"a": "running", "b": "pending"}

        # The overlay is display-only — the DB row itself is untouched.
        assert [item["status"] for item in list_queue_items(self.conn) if item["prompt_id"] == "a"] == ["pending"]

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

    @unittest_run_loop
    async def test_manual_pause_sets_state_and_reflects_in_status(self):
        resp = await self.client.post("/smart_queue/manual_pause", json={"paused": True})
        assert resp.status == 200
        assert self.state.manual_paused is True

        status_resp = await self.client.get("/smart_queue/status")
        body = await status_resp.json()
        assert body["is_paused"] is True
        assert "Manually paused" in body["reasons"]

    @unittest_run_loop
    async def test_manual_resume_clears_manual_pause(self):
        self.state.set_manual_pause(True)
        resp = await self.client.post("/smart_queue/manual_pause", json={"paused": False})
        assert resp.status == 200
        assert self.state.manual_paused is False

    @unittest_run_loop
    async def test_manual_pause_holds_pending_prompt_queue_items(self):
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})]

        resp = await self.client.post("/smart_queue/manual_pause", json={"paused": True})
        body = await resp.json()

        assert body["held"] == 2
        assert self.prompt_queue.queue == []
        assert self.queue_hold.has_held is True

    @unittest_run_loop
    async def test_manual_resume_releases_held_items_back_into_prompt_queue(self):
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]
        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        resp = await self.client.post("/smart_queue/manual_pause", json={"paused": False})
        body = await resp.json()

        assert body["released"] == 1
        assert [item[1] for item in self.prompt_queue.put_calls] == ["a"]
        assert self.queue_hold.has_held is False

    @unittest_run_loop
    async def test_manual_pause_mirrors_held_items_into_sqlite_for_crash_recovery(self):
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]

        await self.client.post("/smart_queue/manual_pause", json={"paused": True})
        assert [item[1] for item in load_held_items(self.conn)] == ["a"]

        await self.client.post("/smart_queue/manual_pause", json={"paused": False})
        assert load_held_items(self.conn) == []

    @unittest_run_loop
    async def test_manual_pause_keeps_never_synced_items_visible_in_panel_queue(self):
        # A job the periodic queue_tracker tick hasn't synced into queue_items
        # yet (it's only ever lived in ComfyUI's own PromptQueue) must still
        # show up in the Smart Queue panel once it's held, not vanish.
        extra_data = {"extra_pnginfo": {"workflow": {"extra": {"workflow_name": "Cyberpunk Cat"}}}}
        self.prompt_queue.queue = [(1.0, "never-synced", {}, extra_data, [], {})]

        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert [item["prompt_id"] for item in body["items"]] == ["never-synced"]
        assert body["items"][0]["name"] == "Cyberpunk Cat"

    @unittest_run_loop
    async def test_manual_pause_does_not_duplicate_an_already_synced_item(self):
        add_queue_item(self.conn, prompt_id="a", name="Already Synced")
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]

        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert [item["prompt_id"] for item in body["items"]] == ["a"]
        assert body["items"][0]["name"] == "Already Synced"

    @unittest_run_loop
    async def test_manual_pause_marks_held_items_with_held_status(self):
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]

        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert body["items"][0]["status"] == "held"

    @unittest_run_loop
    async def test_manual_resume_reverts_status_back_to_pending(self):
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]
        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        await self.client.post("/smart_queue/manual_pause", json={"paused": False})

        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert body["items"][0]["status"] == "pending"

    @unittest_run_loop
    async def test_continue_signals_a_waiting_node(self):
        import asyncio

        prompt_id = "test-prompt-id"
        released = {"done": False}

        async def waiter():
            await asyncio.to_thread(wait_for_continue, prompt_id)
            released["done"] = True

        wait_task = asyncio.ensure_future(waiter())
        await asyncio.sleep(0.05)
        assert released["done"] is False

        resp = await self.client.post(f"/smart_queue/continue/{prompt_id}")
        assert resp.status == 200

        await asyncio.wait_for(wait_task, timeout=2.0)
        assert released["done"] is True

    @unittest_run_loop
    async def test_cancel_wait_raises_interrupt_in_the_waiting_node(self):
        import asyncio

        prompt_id = "test-prompt-id-cancel"
        result = {"exc": None}

        async def waiter():
            try:
                await asyncio.to_thread(wait_for_continue, prompt_id)
            except InterruptProcessingException as exc:
                result["exc"] = exc

        wait_task = asyncio.ensure_future(waiter())
        await asyncio.sleep(0.05)
        assert result["exc"] is None

        resp = await self.client.post(f"/smart_queue/cancel_wait/{prompt_id}")
        assert resp.status == 200

        await asyncio.wait_for(wait_task, timeout=2.0)
        assert isinstance(result["exc"], InterruptProcessingException)

    @unittest_run_loop
    async def test_post_rename_updates_item_name(self):
        add_queue_item(self.conn, prompt_id="a", name="Old Name")
        resp = await self.client.post("/smart_queue/rename", json={"prompt_id": "a", "name": "New Name"})
        assert resp.status == 200
        resp2 = await self.client.get("/smart_queue/queue")
        body = await resp2.json()
        assert body["items"][0]["name"] == "New Name"

    @unittest_run_loop
    async def test_post_rename_rejects_blank_name(self):
        add_queue_item(self.conn, prompt_id="a", name="Old Name")
        resp = await self.client.post("/smart_queue/rename", json={"prompt_id": "a", "name": "   "})
        assert resp.status == 400

    @unittest_run_loop
    async def test_get_queue_filters_by_status_query_param(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        set_queue_item_status(self.conn, prompt_id="b", status="held")
        resp = await self.client.get("/smart_queue/queue?status=held")
        body = await resp.json()
        assert [item["prompt_id"] for item in body["items"]] == ["b"]

    @unittest_run_loop
    async def test_get_history_filters_by_name_query_param(self):
        add_queue_item(self.conn, prompt_id="a", name="Cyberpunk Cat")
        from backend.persistence import mark_completed
        mark_completed(self.conn, prompt_id="a")
        resp = await self.client.get("/smart_queue/history?name=cyber")
        body = await resp.json()
        assert len(body["items"]) == 1
        resp2 = await self.client.get("/smart_queue/history?name=nomatch")
        body2 = await resp2.json()
        assert len(body2["items"]) == 0

    @unittest_run_loop
    async def test_cancel_removes_from_queue_and_db(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})]

        resp = await self.client.post("/smart_queue/cancel", json={"prompt_ids": ["a"]})
        body = await resp.json()

        assert body["cancelled"] == 1
        assert [item["prompt_id"] for item in list_queue_items(self.conn)] == ["b"]
        assert [item[1] for item in self.prompt_queue.queue] == ["b"]

    @unittest_run_loop
    async def test_cancel_with_requeue_keeps_db_row_and_moves_it_to_the_back(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {})]

        resp = await self.client.post("/smart_queue/cancel", json={"prompt_ids": ["a"], "requeue": True})
        body = await resp.json()

        assert body["cancelled"] == 1
        assert body["requeued"] == 1
        assert "a" in [item["prompt_id"] for item in list_queue_items(self.conn)]
        assert self.prompt_queue.put_calls[-1][1] == "a"

    @unittest_run_loop
    async def test_cancel_removes_orphaned_row_whose_job_is_gone_from_comfyui(self):
        # Regression test: found live when a job was still "pending" in
        # PromptQueue.queue when ComfyUI's process was killed/restarted
        # (e.g. an unclean restart) — the job is gone from ComfyUI for good,
        # but the queue_items row is neither running, held, nor in
        # prompt_queue.queue, so it used to sit stuck as "pending" forever
        # and Cancel silently no-op'd on it (cancel_queue_item found nothing
        # to remove from the real queue).
        add_queue_item(self.conn, prompt_id="ghost", name="Orphaned Job")
        self.prompt_queue.queue = []  # gone from the real queue entirely

        resp = await self.client.post("/smart_queue/cancel", json={"prompt_ids": ["ghost"]})
        body = await resp.json()

        assert body["cancelled"] == 1
        assert list_queue_items(self.conn) == []

    @unittest_run_loop
    async def test_cancel_never_removes_a_currently_running_job(self):
        add_queue_item(self.conn, prompt_id="running-job", name="Still Running")
        self.prompt_queue.queue = []
        self.prompt_queue.running = [(1.0, "running-job", {}, {}, [], {})]

        resp = await self.client.post("/smart_queue/cancel", json={"prompt_ids": ["running-job"]})
        body = await resp.json()

        assert body["cancelled"] == 0
        assert [item["prompt_id"] for item in list_queue_items(self.conn)] == ["running-job"]

    @unittest_run_loop
    async def test_cancel_prunes_a_held_job_from_queue_hold(self):
        # Held items are skipped by cancel_queue_item (manual pause owns
        # them, per §14), but the panel still offers Cancel on those rows —
        # it used to silently no-op (spec §26.2). Cancelling now removes the
        # job from QueueHold itself rather than touching prompt_queue, which
        # a held item was never put into in the first place.
        add_queue_item(self.conn, prompt_id="held-job", name="Held")
        self.prompt_queue.queue = []
        self.queue_hold.restore([(1.0, "held-job", {}, {}, [], {})])

        resp = await self.client.post("/smart_queue/cancel", json={"prompt_ids": ["held-job"]})
        body = await resp.json()

        assert body["cancelled"] == 1
        assert list_queue_items(self.conn) == []
        assert self.queue_hold.has_held is False

    @unittest_run_loop
    async def test_cancel_requeue_moves_a_held_job_to_the_back_of_the_held_list(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        self.prompt_queue.queue = []
        self.queue_hold.restore([(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})])

        resp = await self.client.post(
            "/smart_queue/cancel", json={"prompt_ids": ["a"], "requeue": True}
        )
        body = await resp.json()

        assert body["cancelled"] == 1
        assert body["requeued"] == 1
        assert [item[1] for item in self.queue_hold.items] == ["b", "a"]
        # still held, not put into the live queue while paused
        assert self.prompt_queue.queue == []

    @unittest_run_loop
    async def test_reorder_while_paused_changes_release_order_not_just_display_order(self):
        # Regression test: a reorder made while items are held used to only
        # change the SQLite display order — release_held would still put
        # items back in their original captured order on resume, silently
        # discarding the reorder. Live-verified against a running ComfyUI
        # instance (timestamps showed the dragged-to-front job actually ran
        # second) before this fix.
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})]
        await self.client.post("/smart_queue/manual_pause", json={"paused": True})

        resp = await self.client.post("/smart_queue/reorder", json={"ordered_prompt_ids": ["b", "a"]})
        assert resp.status == 200

        await self.client.post("/smart_queue/manual_pause", json={"paused": False})
        # Checking prompt_id order alone isn't enough: PromptQueue.queue is a
        # heap ordered by item[0] (number), not by put() call order, so the
        # numbers themselves must actually be renumbered ascending in the new
        # order — a second real bug this same live-verification pass found.
        put_ids_by_number = [item[1] for item in sorted(self.prompt_queue.put_calls, key=lambda x: x[0])]
        assert put_ids_by_number == ["b", "a"]

    @unittest_run_loop
    async def test_reorder_renumbers_the_real_prompt_queue(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        self.prompt_queue.queue = [(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})]

        resp = await self.client.post("/smart_queue/reorder", json={"ordered_prompt_ids": ["b", "a"]})
        assert resp.status == 200

        final_ids_by_number = sorted(self.prompt_queue.queue, key=lambda x: x[0])
        assert [item[1] for item in final_ids_by_number] == ["b", "a"]
        assert [item["prompt_id"] for item in list_queue_items(self.conn)] == ["b", "a"]
