"""In-memory autopilot state, mutated by the polling loop and read by the middleware."""

from .autopilot import Decision
from .gpu_monitor import GpuMetrics


class AutopilotState:
    def __init__(self) -> None:
        self.is_paused: bool = False
        self.jobs_since_resume: int = 0
        self.last_reasons: tuple[str, ...] = ()
        self.manual_paused: bool = False
        self.last_metrics: GpuMetrics | None = None
        # Monotonic timestamp the job-count break began, or None outside one.
        # The clock itself lives in autopilot_loop (this class stays I/O-free).
        self.break_started_at: float | None = None

    def apply(self, decision: Decision) -> None:
        was_paused = self.is_paused
        self.is_paused = decision.should_pause
        self.last_reasons = decision.reasons
        if was_paused and not self.is_paused:
            self.jobs_since_resume = 0

    def record_metrics(self, metrics: GpuMetrics) -> None:
        self.last_metrics = metrics

    def record_job_started(self) -> None:
        self.jobs_since_resume += 1

    def start_break(self, now: float) -> None:
        self.break_started_at = now

    def end_break(self) -> None:
        """Ends the job-count break by clearing the counter that triggered it.

        Resetting jobs_since_resume here (rather than in apply()) is what makes
        the pause releasable at all: apply() only zeroes it on a paused->
        unpaused transition, which the job-count rule can never reach on its
        own — it keeps firing off the very counter that transition was meant
        to clear, so the queue stayed paused forever."""
        self.break_started_at = None
        self.jobs_since_resume = 0

    def set_manual_pause(self, paused: bool) -> None:
        self.manual_paused = paused

    @property
    def effective_paused(self) -> bool:
        """What the middleware actually gates on: autopilot OR a manual pause."""
        return self.is_paused or self.manual_paused

    @property
    def effective_reasons(self) -> tuple[str, ...]:
        reasons = self.last_reasons
        if self.manual_paused:
            reasons = ("Manually paused",) + reasons
        return reasons
