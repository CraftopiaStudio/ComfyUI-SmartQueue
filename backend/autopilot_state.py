"""In-memory autopilot state, mutated by the polling loop and read by the middleware."""

from .autopilot import Decision


class AutopilotState:
    def __init__(self) -> None:
        self.is_paused: bool = False
        self.jobs_since_resume: int = 0
        self.last_reasons: tuple[str, ...] = ()

    def apply(self, decision: Decision) -> None:
        was_paused = self.is_paused
        self.is_paused = decision.should_pause
        self.last_reasons = decision.reasons
        if was_paused and not self.is_paused:
            self.jobs_since_resume = 0

    def record_job_started(self) -> None:
        self.jobs_since_resume += 1
