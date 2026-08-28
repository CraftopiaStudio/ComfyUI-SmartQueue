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
from .backend.persistence import init_db
from .backend.queue_middleware import create_queue_middleware
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

TICK_INTERVAL_SECONDS = 5.0


async def _async_poll_gpu_metrics():
    # poll_gpu_metrics is a blocking subprocess call; keep it off the event loop.
    return await asyncio.to_thread(poll_gpu_metrics)


async def _autopilot_background_loop():
    while True:
        if _autopilot_settings.master_enabled:
            await run_autopilot_tick(_autopilot_state, _autopilot_settings, _async_poll_gpu_metrics)
        await asyncio.sleep(TICK_INTERVAL_SECONDS)


def _is_autopilot_enabled() -> bool:
    return _autopilot_settings.master_enabled


if _HAS_COMFY_SERVER:
    _db_path = str(Path(__file__).parent / "smart_queue.sqlite3")
    _conn = init_db(_db_path)

    _server = PromptServer.instance
    _server.app.middlewares.append(create_queue_middleware(_autopilot_state, _is_autopilot_enabled))
    register_routes(_server.app, _conn, _autopilot_state, _autopilot_settings)

    async def _start_autopilot_loop(app):
        app["smart_queue_autopilot_task"] = asyncio.create_task(_autopilot_background_loop())

    _server.app.on_startup.append(_start_autopilot_loop)

    logger.info("[Smart Queue] Loaded — autopilot + Smart Cooldown & Pause node registered.")
