"""HTTP endpoints for the Smart Queue panel. Additive only — never overrides
a native ComfyUI route."""

import sqlite3
from dataclasses import asdict

from aiohttp import web

from .autopilot import AutopilotSettings
from .autopilot_state import AutopilotState
from .continue_registry import signal_continue
from .persistence import (
    add_queue_item,
    list_history,
    list_queue_items,
    reorder_queue_items,
    save_held_items,
    set_queue_item_status,
)
from .queue_hold import QueueHold
from .queue_tracker import extract_job_name


def register_routes(
    app: web.Application,
    conn: sqlite3.Connection,
    state: AutopilotState,
    settings: AutopilotSettings,
    queue_hold: QueueHold | None = None,
    prompt_queue=None,
) -> None:
    async def get_status(request: web.Request) -> web.Response:
        metrics = state.last_metrics
        return web.json_response({
            "is_paused": state.effective_paused,
            "reasons": list(state.effective_reasons),
            "manual_paused": state.manual_paused,
            "autopilot_paused": state.is_paused,
            "temp_c": metrics.temp_c if metrics else None,
            "vram_used_mb": metrics.vram_used_mb if metrics else None,
            "vram_total_mb": metrics.vram_total_mb if metrics else None,
        })

    async def post_manual_pause(request: web.Request) -> web.Response:
        payload = await request.json()
        was_paused = state.manual_paused
        now_paused = bool(payload.get("paused", True))
        state.set_manual_pause(now_paused)

        held = released = 0
        if queue_hold is not None and prompt_queue is not None:
            if now_paused and not was_paused:
                held = queue_hold.hold_pending(prompt_queue)
                save_held_items(conn, queue_hold.items)
                # A held item may never have been synced into queue_items by
                # the periodic queue_tracker tick (backend/queue_tracker.py) —
                # e.g. paused within the same tick window it was submitted in.
                # Without this it silently drops out of the panel's list even
                # though it's safely held.
                known_ids = {row["prompt_id"] for row in list_queue_items(conn)}
                for item in queue_hold.items:
                    prompt_id = item[1]
                    if prompt_id not in known_ids:
                        extra_data = item[3] if len(item) > 3 else {}
                        add_queue_item(conn, prompt_id=prompt_id, name=extract_job_name(extra_data))
                    set_queue_item_status(conn, prompt_id=prompt_id, status="held")
            elif was_paused and not now_paused:
                held_snapshot = queue_hold.items
                released = queue_hold.release_held(prompt_queue)
                save_held_items(conn, queue_hold.items)
                for item in held_snapshot:
                    set_queue_item_status(conn, prompt_id=item[1], status="pending")

        return web.json_response({
            "ok": True,
            "manual_paused": state.manual_paused,
            "held": held,
            "released": released,
        })

    async def get_queue(request: web.Request) -> web.Response:
        return web.json_response({"items": list_queue_items(conn)})

    async def post_reorder(request: web.Request) -> web.Response:
        payload = await request.json()
        reorder_queue_items(conn, payload["ordered_prompt_ids"])
        return web.json_response({"ok": True})

    async def get_history(request: web.Request) -> web.Response:
        return web.json_response({"items": list_history(conn)})

    async def get_settings(request: web.Request) -> web.Response:
        return web.json_response(asdict(settings))

    async def post_settings(request: web.Request) -> web.Response:
        payload = await request.json()
        settings.update_from_dict(payload)
        return web.json_response({"ok": True})

    async def post_continue(request: web.Request) -> web.Response:
        prompt_id = request.match_info["prompt_id"]
        signal_continue(prompt_id)
        return web.json_response({"ok": True})

    app.router.add_get("/smart_queue/status", get_status)
    app.router.add_get("/smart_queue/queue", get_queue)
    app.router.add_post("/smart_queue/reorder", post_reorder)
    app.router.add_get("/smart_queue/history", get_history)
    app.router.add_get("/smart_queue/settings", get_settings)
    app.router.add_post("/smart_queue/settings", post_settings)
    app.router.add_post("/smart_queue/continue/{prompt_id}", post_continue)
    app.router.add_post("/smart_queue/manual_pause", post_manual_pause)
