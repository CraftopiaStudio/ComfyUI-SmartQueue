import inspect

from backend.gpu_monitor import GpuMetrics
from backend.nodes.cooldown import SmartCooldownNode, pick_notify_sound, run_cooldown
from backend.sound_library import NO_CUSTOM_SOUND, custom_sounds_dir


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
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0),
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
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0),
        unload_fn=lambda: unload_calls.append(True),
    )
    assert unload_calls == [True]


def test_clear_cache_called_after_unload_when_enabled():
    calls = []
    run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=False,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=True,
        clear_cache_before_wait=True,
        sleep_fn=lambda s: None,
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0),
        unload_fn=lambda: calls.append("unload"),
        cache_fn=lambda: calls.append("cache"),
    )
    assert calls == ["unload", "cache"]


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
        metrics_fn=lambda: GpuMetrics(next(temps), 1000.0, 8000.0),
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
        metrics_fn=lambda: GpuMetrics(None, None, None),
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
        metrics_fn=lambda: GpuMetrics(90.0, 1000.0, 8000.0),
        unload_fn=lambda: None,
        clock_fn=_make_fake_clock(elapsed),
    )
    assert "Max wait" in status


def _make_fake_clock(elapsed):
    def clock():
        return elapsed["total"]
    return clock


def test_max_wait_counts_real_time_when_a_clock_is_supplied():
    # Each poll "takes" 4s of wall clock on top of the 5s sleep, so the 30s
    # budget is spent after 4 polls, not the 6 that counting poll_interval
    # alone would allow.
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds + 4.0

    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=30.0,
        unload_models_before_wait=False,
        sleep_fn=sleep,
        metrics_fn=lambda: GpuMetrics(80.0, 1000.0, 8000.0),
        unload_fn=lambda: None,
        clock_fn=clock,
    )
    assert "Max wait (30s) reached" in status
    assert now[0] == 36.0


def test_without_a_clock_the_loop_still_counts_poll_intervals():
    sleeps = []
    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=30.0,
        unload_models_before_wait=False,
        sleep_fn=sleeps.append,
        metrics_fn=lambda: GpuMetrics(80.0, 1000.0, 8000.0),
        unload_fn=lambda: None,
    )
    assert "Max wait (30s) reached" in status
    assert len(sleeps) == 6


def _drop_sound(root, name="beep.wav"):
    folder = custom_sounds_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"RIFF")


def test_custom_sound_from_the_dropdown_goes_to_the_browser_as_a_web_path(tmp_path):
    root = tmp_path / "web"
    _drop_sound(root)

    assert pick_notify_sound("Custom...", "beep.wav", root=root) == ("Custom...", "sounds/custom/beep.wav", None)


def test_custom_sound_saved_in_the_old_path_form_still_plays(tmp_path):
    root = tmp_path / "web"
    _drop_sound(root)

    assert pick_notify_sound("Custom...", "sounds/custom/beep.wav", root=root)[1] == "sounds/custom/beep.wav"


def test_missing_custom_sound_falls_back_to_default_and_says_why(tmp_path):
    choice, path, warning = pick_notify_sound("Custom...", "gone.wav", root=tmp_path / "web")

    assert (choice, path) == ("Default", "")
    assert "gone.wav" in warning


def test_custom_choice_with_none_selected_says_no_file_set(tmp_path):
    _choice, _path, warning = pick_notify_sound("Custom...", NO_CUSTOM_SOUND, root=tmp_path / "web")

    assert "no file set" in warning


def test_builtin_choices_ignore_the_custom_widget(tmp_path):
    assert pick_notify_sound("Chime", "beep.wav", root=tmp_path / "web") == ("Chime", "", None)


def test_validate_inputs_only_takes_over_the_sound_dropdown():
    # ComfyUI skips its own list/min/max checks for exactly the inputs this
    # signature names, and for every input once it takes **kwargs.
    spec = inspect.getfullargspec(SmartCooldownNode.validate_inputs)

    assert spec.args[1:] == ["custom_sound_path"]
    assert spec.varkw is None


def test_validate_inputs_accepts_values_saved_before_the_dropdown():
    assert SmartCooldownNode.validate_inputs(custom_sound_path="") is True
    assert SmartCooldownNode.validate_inputs(custom_sound_path="sounds/custom/beep.wav") is True
