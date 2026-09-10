"""Non-intrusive middleware: never replaces /prompt, only gates it while paused."""

from typing import Callable

from aiohttp import web

from .autopilot_state import AutopilotState


# ComfyUI serves every route twice: the bare path and an /api-prefixed copy
# (server.py add_routes). The bundled frontend's apiURL() always calls the
# prefixed one, so gating "/prompt" alone gated nothing the UI ever hit.
_GATED_PATHS = ("/prompt", "/api/prompt")


def create_queue_middleware(state: AutopilotState, is_enabled: Callable[[], bool]):
    @web.middleware
    async def queue_middleware(request: web.Request, handler):
        if (
            is_enabled()
            and state.effective_paused
            and request.path in _GATED_PATHS
            and request.method == "POST"
        ):
            reason = "; ".join(state.effective_reasons) or "Queue paused"
            return web.json_response({"error": f"Queue paused: {reason}"}, status=423)
        return await handler(request)

    return queue_middleware
