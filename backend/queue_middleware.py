"""Non-intrusive middleware: never replaces /prompt, only gates it while paused."""

from typing import Callable

from aiohttp import web

from .autopilot_state import AutopilotState


def create_queue_middleware(state: AutopilotState, is_enabled: Callable[[], bool]):
    @web.middleware
    async def queue_middleware(request: web.Request, handler):
        if is_enabled() and state.is_paused and request.path == "/prompt" and request.method == "POST":
            reason = "; ".join(state.last_reasons) or "Queue paused"
            return web.json_response({"error": f"Queue paused: {reason}"}, status=423)
        return await handler(request)

    return queue_middleware
