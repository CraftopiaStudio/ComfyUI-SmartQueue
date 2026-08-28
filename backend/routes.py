"""HTTP endpoints for the Smart Queue panel. Additive only — never overrides
a native ComfyUI route."""

import sqlite3
from dataclasses import asdict

from aiohttp import web

from .autopilot import AutopilotSettings
from .autopilot_state import AutopilotState
from .continue_registry import signal_continue
from .persistence import list_history, list_queue_items, reorder_queue_items


def register_routes(
    app: web.Application,
    conn: sqlite3.Connection,
    state: AutopilotState,
    settings: AutopilotSettings,
) -> None:
    async def get_status(request: web.Request) -> web.Response:
        return web.json_response({
            "is_paused": state.effective_paused,
            "reasons": list(state.effective_reasons),
            "manual_paused": state.manual_paused,
            "autopilot_paused": state.is_paused,
        })

    async def post_manual_pause(request: web.Request) -> web.Response:
        payload = await request.json()
        state.set_manual_pause(bool(payload.get("paused", True)))
        return web.json_response({"ok": True, "manual_paused": state.manual_paused})

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
