"""Background polling: one tick = poll GPU metrics, evaluate rules, apply decision.

Fail-open: if metrics_provider raises, the tick is a no-op — it never leaves
or forces the queue into a paused state due to an internal error.
"""

import logging
from typing import Awaitable, Callable

from .autopilot import AutopilotSettings, evaluate
from .autopilot_state import AutopilotState
from .gpu_monitor import GpuMetrics

logger = logging.getLogger(__name__)

MetricsProvider = Callable[[], Awaitable[GpuMetrics]]


async def run_autopilot_tick(
    state: AutopilotState,
    settings: AutopilotSettings,
    metrics_provider: MetricsProvider,
) -> None:
    try:
        metrics = await metrics_provider()
    except Exception:
        logger.warning("Smart Queue: GPU metrics poll failed, skipping this tick", exc_info=True)
        return

    decision = evaluate(
        metrics,
        jobs_since_resume=state.jobs_since_resume,
        currently_paused=state.is_paused,
        settings=settings,
    )
    state.apply(decision)
