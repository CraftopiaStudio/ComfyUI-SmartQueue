from backend.autopilot import AutopilotSettings
from backend.autopilot_state import AutopilotState
from backend.vram_free_on_pause import maybe_free_vram_on_pause


def make_calls():
    unload_calls = []
    cache_calls = []
    return unload_calls, cache_calls, unload_calls.append, cache_calls.append


def test_does_nothing_when_setting_disabled():
    state = AutopilotState()
    state.set_manual_pause(True)
    settings = AutopilotSettings(free_vram_on_pause=False)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    result = maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))

    assert result is False
    assert unload_calls == []
    assert cache_calls == []


def test_does_nothing_when_not_paused():
    state = AutopilotState()
    settings = AutopilotSettings(free_vram_on_pause=True)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    result = maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))

    assert result is False
    assert unload_calls == []


def test_does_nothing_while_a_job_is_still_running():
    state = AutopilotState()
    state.set_manual_pause(True)
    settings = AutopilotSettings(free_vram_on_pause=True)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    result = maybe_free_vram_on_pause(
        state, settings, running=[(0, "a", {}, {}, [], {})], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True)
    )

    assert result is False
    assert unload_calls == []


def test_frees_vram_once_paused_and_running_is_empty():
    state = AutopilotState()
    state.set_manual_pause(True)
    settings = AutopilotSettings(free_vram_on_pause=True)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    result = maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))

    assert result is True
    assert unload_calls == [True]
    assert cache_calls == [True]
    assert state.vram_freed_for_pause is True


def test_only_fires_once_per_pause_period():
    state = AutopilotState()
    state.set_manual_pause(True)
    settings = AutopilotSettings(free_vram_on_pause=True)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))
    result = maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))

    assert result is False
    assert unload_calls == [True]


def test_resets_and_can_fire_again_after_a_new_pause_period():
    state = AutopilotState()
    settings = AutopilotSettings(free_vram_on_pause=True)
    unload_calls, cache_calls, unload_fn, cache_fn = make_calls()

    state.set_manual_pause(True)
    maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))
    state.set_manual_pause(False)
    maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))
    state.set_manual_pause(True)
    result = maybe_free_vram_on_pause(state, settings, running=[], unload_fn=lambda: unload_fn(True), cache_fn=lambda: cache_fn(True))

    assert result is True
    assert unload_calls == [True, True]
