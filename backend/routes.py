"""HTTP endpoints for the Smart Queue panel. Additive only — never overrides
a native ComfyUI route."""

import sqlite3
from dataclasses import asdict

from aiohttp import web

from .autopilot import AutopilotSettings
from .autopilot_state import AutopilotState
from .continue_registry import signal_cancel, signal_continue
from .native_dialog import browse_path
from .sound_library import import_sound
from .persistence import (
    add_queue_item,
    list_history,
    list_queue_items,
    remove_queue_item,
    rename_queue_item,
    reorder_queue_items,
    save_held_items,
    set_queue_item_status,
)
from .queue_hold import (
    QueueHold,
    cancel_queue_item,
    reorder_pending_queue,
    requeue_item_at_back,
)
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
        items = list_queue_items(
            conn,
            status=request.query.get("status"),
            name_contains=request.query.get("name"),
        )
        # queue_tracker never persists a "running" status onto a row (only
        # "pending"/"held") — overlay it here at read time from the live
        # PromptQueue instead, so a currently-executing job's row still
        # shows "pending" in the DB (untouched, no extra write on every
        # tick) but the panel can tell it apart from a job that's actually
        # waiting behind the pause gate.
        if prompt_queue is not None:
            try:
                running, _queued = prompt_queue.get_current_queue_volatile()
                running_ids = {job[1] for job in running}
            except Exception:
                running_ids = set()
            for item in items:
                if item["prompt_id"] in running_ids:
                    item["status"] = "running"
        return web.json_response({"items": items})

    async def post_reorder(request: web.Request) -> web.Response:
        payload = await request.json()
        ordered_prompt_ids = payload["ordered_prompt_ids"]
        reorder_queue_items(conn, ordered_prompt_ids)
        if prompt_queue is not None:
            reorder_pending_queue(prompt_queue, ordered_prompt_ids)
        if queue_hold is not None and queue_hold.has_held:
            queue_hold.reorder_held(ordered_prompt_ids)
            save_held_items(conn, queue_hold.items)
        return web.json_response({"ok": True})

    async def post_rename(request: web.Request) -> web.Response:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        if not name:
            return web.json_response({"error": "name must not be blank"}, status=400)
        rename_queue_item(conn, prompt_id=payload["prompt_id"], name=name)
        return web.json_response({"ok": True})

    async def post_cancel(request: web.Request) -> web.Response:
        payload = await request.json()
        prompt_ids = payload.get("prompt_ids", [])
        requeue = bool(payload.get("requeue", False))
        cancelled = requeued = 0
        if prompt_queue is not None:
            held_ids = {item[1] for item in queue_hold.items} if queue_hold is not None else set()
            running_ids = {item[1] for item in prompt_queue.get_current_queue_volatile()[0]}
            for prompt_id in prompt_ids:
                item = cancel_queue_item(prompt_queue, prompt_id)
                if item is None:
                    # Not in the real pending queue. Never touch a genuinely
                    # running job (matches "never interrupt a running job").
                    # A held job is prunable though: it can't be reached via
                    # cancel_queue_item (manual pause owns it, per §14) but
                    # the panel still offers Cancel on those rows, so handle
                    # it against QueueHold directly instead of silently
                    # no-oping (spec §26.2). Otherwise it's an orphan: a
                    # tracked row whose underlying ComfyUI job is gone for
                    # good (e.g. ComfyUI was restarted while it was in
                    # flight) — remove the stale row so it doesn't sit stuck
                    # in "pending" forever.
                    if prompt_id in running_ids:
                        continue
                    if prompt_id in held_ids:
                        if queue_hold is None:
                            continue
                        if requeue:
                            if queue_hold.requeue_held_at_back(prompt_id):
                                save_held_items(conn, queue_hold.items)
                                cancelled += 1
                                requeued += 1
                        elif queue_hold.cancel_held(prompt_id) is not None:
                            save_held_items(conn, queue_hold.items)
                            remove_queue_item(conn, prompt_id=prompt_id)
                            cancelled += 1
                        continue
                    remove_queue_item(conn, prompt_id=prompt_id)
                    cancelled += 1
                    continue
                cancelled += 1
                if requeue:
                    requeue_item_at_back(prompt_queue, item)
                    requeued += 1
                else:
                    remove_queue_item(conn, prompt_id=prompt_id)
        return web.json_response({"cancelled": cancelled, "requeued": requeued})

    async def get_history(request: web.Request) -> web.Response:
        items = list_history(
            conn,
            name_contains=request.query.get("name"),
            date_from=request.query.get("date_from"),
            date_to=request.query.get("date_to"),
        )
        return web.json_response({"items": items})

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

    async def post_cancel_wait(request: web.Request) -> web.Response:
        prompt_id = request.match_info["prompt_id"]
        signal_cancel(prompt_id)
        return web.json_response({"ok": True})

    async def post_browse_sound_file(request: web.Request) -> web.Response:
        # import_sound copies the pick into web/sounds/custom and returns a
        # path relative to web/ — the browser cannot load a raw filesystem
        # path, so handing back the picked path directly never worked.
        return await browse_path(
            request,
            pick_folder=False,
            title="Select notification sound",
            transform=import_sound,
        )

    app.router.add_get("/smart_queue/status", get_status)
    app.router.add_get("/smart_queue/queue", get_queue)
    app.router.add_post("/smart_queue/reorder", post_reorder)
    app.router.add_get("/smart_queue/history", get_history)
    app.router.add_get("/smart_queue/settings", get_settings)
    app.router.add_post("/smart_queue/settings", post_settings)
    app.router.add_post("/smart_queue/continue/{prompt_id}", post_continue)
    app.router.add_post("/smart_queue/cancel_wait/{prompt_id}", post_cancel_wait)
    app.router.add_post("/smart_queue/manual_pause", post_manual_pause)
    app.router.add_post("/smart_queue/rename", post_rename)
    app.router.add_post("/smart_queue/cancel", post_cancel)
    app.router.add_post("/smart_queue/browse_sound_file", post_browse_sound_file)
