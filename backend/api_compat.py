"""One-time startup check that ComfyUI's PromptQueue exposes the shape Smart
Queue depends on (get_current_queue_volatile(), get_history()).

Doesn't prevent load on mismatch — same fail-open philosophy as everywhere
else in this codebase — it just turns a silent future breakage into an
immediate, specific warning at startup instead of a generic "autopilot
queue-hold sync failed" buried in the 5s tick loop.
"""

import logging

logger = logging.getLogger(__name__)

_UPDATE_HINT = "Smart Queue may need an update to match it."


def verify_prompt_queue_shape(prompt_queue) -> bool:
    if not hasattr(prompt_queue, "get_current_queue_volatile"):
        logger.warning(
            "[Smart Queue] ComfyUI's PromptQueue has no get_current_queue_volatile() — "
            "autopilot and the sidebar panel will not reflect the real queue. "
            "ComfyUI's internal queue API may have changed; %s",
            _UPDATE_HINT,
        )
        return False

    try:
        running, queued = prompt_queue.get_current_queue_volatile()
    except Exception as exc:
        logger.warning(
            "[Smart Queue] get_current_queue_volatile() raised %r — ComfyUI's internal "
            "queue API may have changed; %s",
            exc,
            _UPDATE_HINT,
        )
        return False

    if not isinstance(running, list) or not isinstance(queued, list):
        logger.warning(
            "[Smart Queue] get_current_queue_volatile() returned an unexpected shape "
            "(%s, %s) instead of (list, list) — ComfyUI's internal queue API may have "
            "changed; %s",
            type(running).__name__,
            type(queued).__name__,
            _UPDATE_HINT,
        )
        return False

    if not hasattr(prompt_queue, "get_history"):
        logger.warning(
            "[Smart Queue] ComfyUI's PromptQueue has no get_history() — history sync "
            "will not work. %s",
            _UPDATE_HINT,
        )
        return False

    return True
