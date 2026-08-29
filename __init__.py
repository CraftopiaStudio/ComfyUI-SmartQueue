"""Smart Queue — GPU-aware queue autopilot + cooldown/pause node for ComfyUI."""

import asyncio
import logging
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
from .backend.persistence import init_db, load_held_items, set_queue_item_status
from .backend.queue_hold import QueueHold
from .backend.queue_middleware import create_queue_middleware
from .backend.queue_tracker import sync_queue_tracker
from .backend.routes import register_routes

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {
    "RubzGpuCooldownNode": SmartCooldownNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "RubzGpuCooldownNode": "Smart Cooldown & Pause",
}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_autopilot_state = AutopilotState()
_autopilot_settings = AutopilotSettings()
_queue_hold = QueueHold()
_seen_running: set = set()
_seen_completed: set = set()

TICK_INTERVAL_SECONDS = 5.0


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


async def _autopilot_background_loop(conn):
    while True:
        if _autopilot_settings.master_enabled:
            await run_autopilot_tick(_autopilot_state, _autopilot_settings, _async_poll_gpu_metrics)
        try:
            # Sync sqlite3 connections are bound to the thread that created them
            # (this loop's thread), so this must run inline, not via asyncio.to_thread.
            _sync_queue_tracker_tick(conn)
        except Exception:
            logger.warning("Smart Queue: queue tracker sync failed, skipping this tick", exc_info=True)
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
