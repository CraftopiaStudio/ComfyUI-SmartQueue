"""Frees VRAM once a pause has actually taken hold — queue-wide equivalent of
the Smart Cooldown node's unload_models/clear_cache toggles (spec §29 #27),
triggered from the pause path itself (manual button or autopilot) rather than
requiring the node in every workflow.

Dependency-injected the same way as backend.nodes.cooldown.run_cooldown, so
this is unit-testable without touching comfy.model_management.
"""

from typing import Callable

from .autopilot import AutopilotSettings
from .autopilot_state import AutopilotState


def maybe_free_vram_on_pause(
    state: AutopilotState,
    settings: AutopilotSettings,
    running: list,
    unload_fn: Callable[[], None],
    cache_fn: Callable[[], None],
) -> bool:
    """Call on every autopilot tick. Returns True the one time it actually
    freed VRAM this pause period."""
    if not state.effective_paused:
        state.vram_freed_for_pause = False
        return False
    if not settings.free_vram_on_pause or state.vram_freed_for_pause:
        return False
    if running:
        # The already-in-flight job hasn't finished yet — freeing now would
        # yank its own models out from under it.
        return False

    unload_fn()
    cache_fn()
    state.vram_freed_for_pause = True
    return True
