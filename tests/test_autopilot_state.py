from backend.autopilot import Decision
from backend.autopilot_state import AutopilotState


def test_initial_state_is_not_paused():
    state = AutopilotState()
    assert state.is_paused is False
    assert state.jobs_since_resume == 0


def test_apply_pause_decision_sets_paused_and_stores_reasons():
    state = AutopilotState()
    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    assert state.is_paused is True
    assert state.last_reasons == ("too hot",)


def test_apply_resume_decision_after_pause_resets_job_counter():
    state = AutopilotState()
    state.record_job_started()
    state.record_job_started()
    state.apply(Decision(should_pause=True, reasons=("cooldown",)))
    state.apply(Decision(should_pause=False, reasons=()))
    assert state.is_paused is False
    assert state.jobs_since_resume == 0


def test_record_job_started_increments_counter():
    state = AutopilotState()
    state.record_job_started()
    state.record_job_started()
    assert state.jobs_since_resume == 2


def test_apply_resume_when_already_resumed_does_not_reset_counter():
    state = AutopilotState()
    state.record_job_started()
    state.apply(Decision(should_pause=False, reasons=()))
    assert state.jobs_since_resume == 1


def test_manual_pause_alone_sets_effective_paused():
    state = AutopilotState()
    state.set_manual_pause(True)
    assert state.effective_paused is True
    assert state.is_paused is False
    assert "Manually paused" in state.effective_reasons


def test_effective_paused_false_when_neither_autopilot_nor_manual():
    state = AutopilotState()
    assert state.effective_paused is False
    assert state.effective_reasons == ()


def test_effective_reasons_combines_manual_and_autopilot_reasons():
    state = AutopilotState()
    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    state.set_manual_pause(True)
    assert state.effective_reasons == ("Manually paused", "too hot")


def test_manual_resume_clears_manual_paused_but_not_autopilot():
    state = AutopilotState()
    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    state.set_manual_pause(True)
    state.set_manual_pause(False)
    assert state.manual_paused is False
    assert state.effective_paused is True  # autopilot still says pause


def test_consume_effective_pause_transition_is_none_when_never_paused():
    state = AutopilotState()
    assert state.consume_effective_pause_transition() is None


def test_consume_effective_pause_transition_reports_held_once():
    state = AutopilotState()
    state.set_manual_pause(True)
    assert state.consume_effective_pause_transition() == "held"
    # Same pause still in effect on a later tick — not a new transition.
    assert state.consume_effective_pause_transition() is None


def test_consume_effective_pause_transition_reports_released_once():
    state = AutopilotState()
    state.set_manual_pause(True)
    state.consume_effective_pause_transition()
    state.set_manual_pause(False)
    assert state.consume_effective_pause_transition() == "released"
    assert state.consume_effective_pause_transition() is None


def test_consume_effective_pause_transition_covers_autopilot_too():
    state = AutopilotState()
    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    assert state.consume_effective_pause_transition() == "held"
    state.apply(Decision(should_pause=False, reasons=()))
    assert state.consume_effective_pause_transition() == "released"


def test_consume_effective_pause_transition_does_not_double_fire_when_both_sources_overlap():
    # Manual pause engages first, then autopilot also wants to pause while
    # manual is still on — effective_paused was already True, so this must
    # not report a second "held". Symmetric on the way down: autopilot
    # resuming first must not report "released" while manual pause still
    # holds effective_paused True.
    state = AutopilotState()
    state.set_manual_pause(True)
    assert state.consume_effective_pause_transition() == "held"

    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    assert state.consume_effective_pause_transition() is None

    state.apply(Decision(should_pause=False, reasons=()))
    assert state.consume_effective_pause_transition() is None  # manual still holds it

    state.set_manual_pause(False)
    assert state.consume_effective_pause_transition() == "released"
