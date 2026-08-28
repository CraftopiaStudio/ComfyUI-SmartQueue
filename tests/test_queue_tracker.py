from backend.autopilot_state import AutopilotState
from backend.persistence import init_db, list_history, list_queue_items
from backend.queue_tracker import extract_job_name, sync_queue_tracker


def make_item(prompt_id, extra_data=None):
    # Mirrors execution.PromptQueue's item tuple: (number, prompt_id, prompt, extra_data, outputs_to_execute, sensitive)
    return (0, prompt_id, {}, extra_data or {}, [], {})


def test_extract_job_name_from_workflow_extra():
    extra_data = {"extra_pnginfo": {"workflow": {"extra": {"workflow_name": "Cyberpunk Cat"}}}}
    assert extract_job_name(extra_data) == "Cyberpunk Cat"


def test_extract_job_name_from_workflow_name_fallback():
    extra_data = {"extra_pnginfo": {"workflow": {"name": "Sunset Beach"}}}
    assert extract_job_name(extra_data) == "Sunset Beach"


def test_extract_job_name_falls_back_to_timestamp_when_missing():
    name = extract_job_name({})
    assert name.startswith("Job ")


def test_sync_adds_new_running_and_queued_items(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]
    queued = [make_item("b")]

    sync_queue_tracker(conn, running, queued, history={}, autopilot_state=state, seen_running=set(), seen_completed=set())

    items = list_queue_items(conn)
    assert {item["prompt_id"] for item in items} == {"a", "b"}


def test_sync_records_job_started_once_per_running_item(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]

    seen_running, seen_completed = sync_queue_tracker(
        conn, running, [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set()
    )
    assert state.jobs_since_resume == 1

    # same item still running on the next tick: must not double-count
    sync_queue_tracker(
        conn, running, [], history={}, autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed
    )
    assert state.jobs_since_resume == 1


def test_sync_does_not_record_job_started_for_queued_items(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    queued = [make_item("a")]

    sync_queue_tracker(conn, [], queued, history={}, autopilot_state=state, seen_running=set(), seen_completed=set())
    assert state.jobs_since_resume == 0


def test_sync_marks_completed_when_item_leaves_queue_and_is_in_history(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]
    seen_running, seen_completed = sync_queue_tracker(
        conn, running, [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set()
    )

    # job finished: no longer running/queued, now in history
    sync_queue_tracker(
        conn, [], [], history={"a": {"prompt": make_item("a")}},
        autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed,
    )

    assert list_queue_items(conn) == []
    history_rows = list_history(conn)
    assert len(history_rows) == 1
    assert history_rows[0]["prompt_id"] == "a"


def test_sync_handles_job_that_starts_and_finishes_within_one_tick(tmp_path):
    """A fast job never observed running/queued by us, only ever seen in history."""
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    extra_data = {"extra_pnginfo": {"workflow": {"name": "Quick Render"}}}
    history = {"a": {"prompt": make_item("a", extra_data)}}

    sync_queue_tracker(conn, [], [], history=history, autopilot_state=state, seen_running=set(), seen_completed=set())

    assert list_queue_items(conn) == []
    history_rows = list_history(conn)
    assert len(history_rows) == 1
    assert history_rows[0]["prompt_id"] == "a"
    assert history_rows[0]["name"] == "Quick Render"


def test_sync_does_not_reprocess_already_completed_history_entries(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    history = {"a": {"prompt": make_item("a")}}

    seen_running, seen_completed = sync_queue_tracker(
        conn, [], [], history=history, autopilot_state=state, seen_running=set(), seen_completed=set()
    )
    assert len(list_history(conn)) == 1

    # same history entry still present on the next tick: must not duplicate
    sync_queue_tracker(
        conn, [], [], history=history, autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed
    )
    assert len(list_history(conn)) == 1
