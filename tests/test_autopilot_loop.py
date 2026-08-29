import pytest

from backend.gpu_monitor import GpuMetrics
from backend.autopilot import AutopilotSettings
from backend.autopilot_state import AutopilotState
from backend.autopilot_loop import run_autopilot_tick


@pytest.mark.asyncio
async def test_tick_pauses_state_when_metrics_exceed_threshold():
    state = AutopilotState()
    settings = AutopilotSettings(temp_rule_enabled=True, pause_temp_c=80.0)

    async def fake_metrics():
        return GpuMetrics(temp_c=90.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    await run_autopilot_tick(state, settings, fake_metrics)
    assert state.is_paused is True


@pytest.mark.asyncio
async def test_tick_leaves_state_unpaused_when_metrics_are_fine():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

    async def fake_metrics():
        return GpuMetrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    await run_autopilot_tick(state, settings, fake_metrics)
    assert state.is_paused is False


@pytest.mark.asyncio
async def test_tick_fails_open_when_metrics_provider_raises():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

    async def broken_metrics():
        raise RuntimeError("nvidia-smi exploded")

    await run_autopilot_tick(state, settings, broken_metrics)
    assert state.is_paused is False


@pytest.mark.asyncio
async def test_tick_fails_open_when_a_setting_has_the_wrong_type():
    # AutopilotSettings.update_from_dict now coerces incoming values to each
    # field's declared type (spec §26.2), so a string from the settings
    # endpoint no longer reaches evaluate() as one. This test instead
    # simulates a bad type landing on the dataclass by some other path, to
    # keep the tick's own fail-open guard covered independently of that fix
    # — the tick runs in a `while True` loop that also drives queue tracking
    # and history retention, so a bad value must not escape it.
    state = AutopilotState()
    settings = AutopilotSettings(temp_rule_enabled=True)
    settings.pause_temp_c = "80"

    async def fake_metrics():
        return GpuMetrics(temp_c=90.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    await run_autopilot_tick(state, settings, fake_metrics)
    assert state.is_paused is False


@pytest.mark.asyncio
async def test_job_count_break_ends_after_its_configured_duration():
    # Regression: the job-count rule used to latch forever. jobs_since_resume
    # was only reset on a paused->unpaused transition, which the rule itself
    # made unreachable — it kept firing off the very counter that transition
    # was meant to clear.
    state = AutopilotState()
    settings = AutopilotSettings(
        job_count_rule_enabled=True, max_jobs_before_pause=3, job_count_break_minutes=5.0
    )

    async def fake_metrics():
        return GpuMetrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    for _ in range(3):
        state.record_job_started()

    now = 0.0
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is True

    now = 299.0
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is True, "break must last the full 5 minutes"

    now = 300.0
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is False
    assert state.jobs_since_resume == 0


@pytest.mark.asyncio
async def test_job_count_break_can_trigger_again_after_a_completed_break():
    state = AutopilotState()
    settings = AutopilotSettings(
        job_count_rule_enabled=True, max_jobs_before_pause=2, job_count_break_minutes=1.0
    )

    async def fake_metrics():
        return GpuMetrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    now = 0.0
    for _ in range(2):
        state.record_job_started()
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is True

    now = 60.0
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is False

    for _ in range(2):
        state.record_job_started()
    now = 61.0
    await run_autopilot_tick(state, settings, fake_metrics, clock=lambda: now)
    assert state.is_paused is True
