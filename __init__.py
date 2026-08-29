"""Smart Queue — GPU-aware queue autopilot + cooldown/pause node for ComfyUI."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from server import PromptServer  # type: ignore[import-not-found]
    _HAS_COMFY_SERVER = True
except ImportError:
    _HAS_COMFY_SERVER = False

from .backend.autopilot import AutopilotSettings
from .backend.autopilot_loop import run_autopilot_tick
from .backend.autopilot_state import AutopilotState
from .backend.gpu_monitor import poll_gpu_metrics
from .backend.nodes.cooldown import SmartCooldownNode
from .backend.persistence import (
    delete_history_older_than,
    init_db,
    load_held_items,
    set_queue_item_status,
)
from .backend.queue_hold import QueueHold
from .backend.queue_hold_sync import sync_queue_hold
from .backend.queue_middleware import create_queue_middleware
from .backend.queue_tracker import sync_queue_tracker
from .backend.routes import register_routes

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {
    "SmartCooldownNode": SmartCooldownNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartCooldownNode": "Smart Cooldown & Pause",
}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_autopilot_state = AutopilotState()
_autopilot_settings = AutopilotSettings()
_queue_hold = QueueHold()
_seen_running: set = set()
_seen_completed: set = set()
_last_history_cleanup: datetime | None = None

TICK_INTERVAL_SECONDS = 5.0
HISTORY_CLEANUP_INTERVAL = timedelta(hours=1)


async def _async_poll_gpu_metrics():
    # poll_gpu_metrics is a blocking subprocess call; keep it off the event loop.
    return await asyncio.to_thread(poll_gpu_metrics)


def _sync_queue_tracker_tick(conn) -> None:
    global _seen_running, _seen_completed
    running, queued = _server.prompt_queue.get_current_queue_volatile()
    history = _server.prompt_queue.get_history()
    _seen_running, _seen_completed = sync_queue_tracker(
        conn, running, queued, history, _autopilot_state, _seen_running, _seen_completed
    )


def _should_run_history_cleanup(now, last_cleanup, retention_days: int) -> bool:
    """Gates the DELETE in the loop below to roughly once an hour instead of
    every 5s tick. A window measured in *days* doesn't need per-tick
    precision, and a DELETE + commit on every tick was ~17,280 mostly-no-op
    write transactions a day (spec §26.2)."""
    if retention_days <= 0:
        return False
    if last_cleanup is None:
        return True
    return now - last_cleanup >= HISTORY_CLEANUP_INTERVAL


async def _autopilot_background_loop(conn):
    global _last_history_cleanup
    while True:
        if _autopilot_settings.master_enabled:
            await run_autopilot_tick(_autopilot_state, _autopilot_settings, _async_poll_gpu_metrics)
        try:
            # Closes the gap the manual pause button already had covered:
            # autopilot flipping is_paused used to only ever gate new
            # POST /prompt submissions (backend/queue_middleware.py), so jobs
            # already queued before the pause kept executing regardless — the
            # exact "20 jobs queued, nobody watching" case the temperature/
            # VRAM rules exist for. Shares the manual-pause route's
            # QueueHold/edge-trigger machinery via sync_queue_hold rather than
            # duplicating it (spec §26.2).
            sync_queue_hold(conn, _autopilot_state, _queue_hold, _server.prompt_queue)
        except Exception:
            logger.warning("Smart Queue: autopilot queue-hold sync failed, skipping this tick", exc_info=True)
        try:
            # Sync sqlite3 connections are bound to the thread that created them
            # (this loop's thread), so this must run inline, not via asyncio.to_thread.
            _sync_queue_tracker_tick(conn)
        except Exception:
            logger.warning("Smart Queue: queue tracker sync failed, skipping this tick", exc_info=True)
        now = datetime.now(timezone.utc)
        if _should_run_history_cleanup(now, _last_history_cleanup, _autopilot_settings.history_retention_days):
            try:
                cutoff = (now - timedelta(days=_autopilot_settings.history_retention_days)).isoformat()
                delete_history_older_than(conn, cutoff)
                _last_history_cleanup = now
            except Exception:
                logger.warning("Smart Queue: history auto-archive failed, skipping this tick", exc_info=True)
        await asyncio.sleep(TICK_INTERVAL_SECONDS)


def _is_autopilot_enabled() -> bool:
    return _autopilot_settings.master_enabled


if _HAS_COMFY_SERVER:
    _db_path = str(Path(__file__).parent / "smart_queue.sqlite3")
    _conn = init_db(_db_path)

    _server = PromptServer.instance

    # held_items is only ever non-empty while a manual pause is in effect (it's
    # cleared on every release) — so finding rows here means the previous
    # process stopped mid-pause. Restore into QueueHold (not straight back into
    # prompt_queue) and keep manual_paused on, so a crash/restart never
    # silently resumes a queue the user deliberately paused.
    _recovered_held_items = load_held_items(_conn)
    if _recovered_held_items:
        _queue_hold.restore(_recovered_held_items)
        _autopilot_state.set_manual_pause(True)
        for _item in _recovered_held_items:
            set_queue_item_status(_conn, prompt_id=_item[1], status="held")
        logger.warning(
            "[Smart Queue] Restored %d held job(s) after a restart — still paused, resume manually when ready.",
            len(_recovered_held_items),
        )

    _server.app.middlewares.append(create_queue_middleware(_autopilot_state, _is_autopilot_enabled))
    register_routes(
        _server.app,
        _conn,
        _autopilot_state,
        _autopilot_settings,
        queue_hold=_queue_hold,
        prompt_queue=_server.prompt_queue,
    )

    async def _start_autopilot_loop(app):
        app["smart_queue_autopilot_task"] = asyncio.create_task(_autopilot_background_loop(_conn))

    _server.app.on_startup.append(_start_autopilot_loop)

    logger.info("[Smart Queue] Loaded — autopilot + Smart Cooldown & Pause node registered.")
