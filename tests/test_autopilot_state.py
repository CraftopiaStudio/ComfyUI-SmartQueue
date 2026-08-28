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
