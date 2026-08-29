"""Pure rule-engine: GPU metrics + queue state -> pause/resume decision.

No I/O here. This module never touches the network, the filesystem, or a
subprocess — that keeps it trivially unit-testable and lets the fail-open
guarantee live entirely in the caller (backend.autopilot_loop).
"""

from dataclasses import dataclass

from .gpu_monitor import GpuMetrics


@dataclass
class AutopilotSettings:
    master_enabled: bool = True

    temp_rule_enabled: bool = False
    pause_temp_c: float = 80.0
    resume_temp_c: float = 72.0

    vram_rule_enabled: bool = False
    min_free_vram_mb: float = 1024.0

    job_count_rule_enabled: bool = False
    max_jobs_before_pause: int = 20

    history_retention_days: int = 30

    def update_from_dict(self, values: dict) -> None:
        """Mutates in place so callers holding a reference (the background
        loop, the middleware's is_enabled closure) see updates immediately."""
        for key, value in values.items():
            if hasattr(self, key):
                setattr(self, key, value)


@dataclass
class Decision:
    should_pause: bool
    reasons: tuple[str, ...]


def evaluate(
    metrics: GpuMetrics,
    jobs_since_resume: int,
    currently_paused: bool,
    settings: AutopilotSettings,
) -> Decision:
    reasons: list[str] = []

    if settings.temp_rule_enabled and metrics.temp_c is not None:
        threshold = settings.resume_temp_c if currently_paused else settings.pause_temp_c
        if metrics.temp_c > threshold:
            reasons.append(f"GPU at {metrics.temp_c:.0f}C, target {threshold:.0f}C")

    if settings.vram_rule_enabled and metrics.vram_free_mb is not None:
        if metrics.vram_free_mb < settings.min_free_vram_mb:
            reasons.append(
                f"Only {metrics.vram_free_mb:.0f}MB VRAM free, below {settings.min_free_vram_mb:.0f}MB"
            )

    if settings.job_count_rule_enabled and jobs_since_resume >= settings.max_jobs_before_pause:
        reasons.append(f"{jobs_since_resume} jobs run since last pause, cooldown break")

    return Decision(should_pause=len(reasons) > 0, reasons=tuple(reasons))
