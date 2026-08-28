from backend.gpu_monitor import GpuMetrics
from backend.nodes.cooldown import run_cooldown


def test_fixed_delay_is_applied():
    sleep_calls = []
    status = run_cooldown(
        fixed_delay_seconds=10.0,
        wait_for_temp=False,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=sleep_calls.append,
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
    )
    assert sleep_calls == [10.0]
    assert "Fixed delay: 10s" in status


def test_unload_models_called_before_wait_when_enabled():
    unload_calls = []
    run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=False,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=True,
        sleep_fn=lambda s: None,
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: unload_calls.append(True),
    )
    assert unload_calls == [True]


def test_waits_for_temp_until_below_target():
    temps = iter([90.0, 80.0, 60.0])
    sleep_calls = []
    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=sleep_calls.append,
        metrics_fn=lambda: GpuMetrics(next(temps), 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
    )
    assert sleep_calls == [5.0, 5.0]
    assert "60" in status


def test_temp_unavailable_skips_temp_wait():
    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=lambda s: None,
        metrics_fn=lambda: GpuMetrics(None, None, None, None),
        unload_fn=lambda: None,
    )
    assert "unavailable" in status.lower()


def test_max_wait_seconds_caps_the_temp_wait():
    elapsed = {"total": 0.0}

    def fake_sleep(seconds):
        elapsed["total"] += seconds

    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=10.0,  # unreachably low, forces the cap
        poll_interval_seconds=5.0,
        max_wait_seconds=12.0,
        unload_models_before_wait=False,
        sleep_fn=fake_sleep,
        metrics_fn=lambda: GpuMetrics(90.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
        clock_fn=_make_fake_clock(elapsed),
    )
    assert "Max wait" in status


def _make_fake_clock(elapsed):
    def clock():
        return elapsed["total"]
    return clock
