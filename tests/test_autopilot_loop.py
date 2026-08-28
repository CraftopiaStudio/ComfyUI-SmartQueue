import pytest

from backend.gpu_monitor import GpuMetrics
from backend.autopilot import AutopilotSettings
from backend.autopilot_state import AutopilotState
from backend.autopilot_loop import run_autopilot_tick


@pytest.mark.asyncio
async def test_tick_pauses_state_when_metrics_exceed_threshold():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

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
