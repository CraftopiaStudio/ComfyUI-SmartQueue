"""Background polling: one tick = poll GPU metrics, evaluate rules, apply decision.

Fail-open: if any part of the tick raises, the tick is a no-op — it never
leaves or forces the queue into a paused state due to an internal error.
"""

import logging
import time
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
    clock: Callable[[], float] = time.monotonic,
) -> None:
    try:
        metrics = await metrics_provider()
    except Exception:
        logger.warning("Smart Queue: GPU metrics poll failed, skipping this tick", exc_info=True)
        return

    state.record_metrics(metrics)

    # The job-count break's clock lives here rather than in evaluate(), which
    # stays a pure function of the metrics and the counters it's handed. Start
    # the break on the tick the count is first reached, and end it once
    # job_count_break_minutes have passed — ending it clears the counter, so
    # evaluate() simply stops reporting the rule and the queue resumes.
    now = clock()
    if state.break_started_at is not None:
        if now - state.break_started_at >= settings.job_count_break_minutes * 60:
            state.end_break()
    elif settings.job_count_rule_enabled and state.jobs_since_resume >= settings.max_jobs_before_pause:
        state.start_break(now)

    # The rule engine is guarded too, not just the metrics poll: settings
    # arrive from POST /smart_queue/settings unvalidated, so a value of the
    # wrong type raises in here rather than in the provider. This tick runs
    # inside the caller's `while True` loop (__init__.py), which also drives
    # queue tracking and history retention — letting an exception escape
    # would end that loop for good and silently stop all three.
    try:
        decision = evaluate(
            metrics,
            jobs_since_resume=state.jobs_since_resume,
            currently_paused=state.is_paused,
            settings=settings,
        )
    except Exception:
        logger.warning("Smart Queue: rule evaluation failed, skipping this tick", exc_info=True)
        return

    state.apply(decision)
