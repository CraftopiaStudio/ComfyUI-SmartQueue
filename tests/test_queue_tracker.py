from backend.autopilot_state import AutopilotState
from backend.persistence import add_queue_item, init_db, list_history, list_queue_items, set_queue_item_status
from backend.queue_tracker import (
    extract_job_name,
    extract_thumbnail_query,
    extract_workflow_json,
    sync_queue_tracker,
)


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


def test_extract_thumbnail_query_from_first_image_output():
    entry = {"outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "sub", "type": "output"}]}}}
    assert extract_thumbnail_query(entry) == "filename=a.png&subfolder=sub&type=output"


def test_extract_thumbnail_query_returns_none_when_no_images():
    assert extract_thumbnail_query({"outputs": {"9": {}}}) is None
    assert extract_thumbnail_query({}) is None
    assert extract_thumbnail_query({"outputs": None}) is None


def test_extract_thumbnail_query_url_encodes_filename():
    entry = {"outputs": {"9": {"images": [{"filename": "my file.png", "subfolder": "", "type": "output"}]}}}
    assert extract_thumbnail_query(entry) == "filename=my+file.png&subfolder=&type=output"


def test_extract_workflow_json_from_extra_pnginfo():
    extra_data = {"extra_pnginfo": {"workflow": {"nodes": [1, 2]}}}
    assert extract_workflow_json(extra_data) == '{"nodes": [1, 2]}'


def test_extract_workflow_json_returns_none_when_missing():
    assert extract_workflow_json({}) is None
    assert extract_workflow_json({"extra_pnginfo": {}}) is None


def test_sync_stores_thumbnail_and_workflow_json_on_completion(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    extra_data = {"extra_pnginfo": {"workflow": {"nodes": []}}}
    history = {
        "a": {
            "prompt": make_item("a", extra_data),
            "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}},
        }
    }

    sync_queue_tracker(conn, [], [], history=history, autopilot_state=state, seen_running=set(), seen_completed=set())

    row = list_history(conn)[0]
    assert row["thumbnail_path"] == "filename=a.png&subfolder=&type=output"
    assert row["workflow_json"] == '{"nodes": []}'


def test_sync_records_started_at_when_job_starts_running(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]

    sync_queue_tracker(conn, running, [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set())

    assert list_queue_items(conn)[0]["started_at"] is not None


def test_sync_leaves_started_at_null_for_job_never_seen_running(tmp_path):
    """Started+finished inside one tick gap (documented edge case) never gets
    a started_at, so its duration_seconds stays NULL downstream (spec §29 #3)."""
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    history = {"a": {"prompt": make_item("a")}}

    sync_queue_tracker(conn, [], [], history=history, autopilot_state=state, seen_running=set(), seen_completed=set())

    assert list_history(conn)[0]["duration_seconds"] is None


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
    assert state.jobs_since_resume == 1


def test_sync_does_not_double_count_job_seen_running_then_in_history(tmp_path):
    """A job seen running on one tick and completed on the next must be counted once."""
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]

    seen_running, seen_completed = sync_queue_tracker(
        conn, running, [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set()
    )
    assert state.jobs_since_resume == 1

    sync_queue_tracker(
        conn, [], [], history={"a": {"prompt": make_item("a")}},
        autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed,
    )
    assert state.jobs_since_resume == 1


def test_sync_prunes_seen_sets_to_what_comfyui_still_shows(tmp_path):
    # seen_running/seen_completed used to grow for the process lifetime
    # (spec §26.2) — once a job drops out of `running` and out of ComfyUI's
    # own `history` dict, nothing needs to remember it anymore.
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    running = [make_item("a")]

    seen_running, seen_completed = sync_queue_tracker(
        conn, running, [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set()
    )
    assert seen_running == {"a"}

    seen_running, seen_completed = sync_queue_tracker(
        conn, [], [], history={"a": {"prompt": make_item("a")}},
        autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed,
    )
    assert seen_running == set()
    assert seen_completed == {"a"}

    # ComfyUI's own history evicted "a": nothing left to forget it for.
    seen_running, seen_completed = sync_queue_tracker(
        conn, [], [], history={}, autopilot_state=state, seen_running=seen_running, seen_completed=seen_completed,
    )
    assert seen_completed == set()


def test_sync_prunes_pending_item_cleared_from_live_queue_without_history(tmp_path):
    """A pending row must not survive forever when its job is removed from
    ComfyUI's live queue by something other than /smart_queue/cancel — e.g.
    the native "Clear Queue" button, which never runs the job and so never
    puts it in history either."""
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    queued = [make_item("a"), make_item("b")]

    sync_queue_tracker(conn, [], queued, history={}, autopilot_state=state, seen_running=set(), seen_completed=set())
    assert {item["prompt_id"] for item in list_queue_items(conn)} == {"a", "b"}

    # "a" gets cleared from ComfyUI's live queue; "b" stays queued.
    sync_queue_tracker(conn, [], [queued[1]], history={}, autopilot_state=state, seen_running=set(), seen_completed=set())

    assert {item["prompt_id"] for item in list_queue_items(conn)} == {"b"}


def test_sync_does_not_prune_held_items(tmp_path):
    """A "held" row is deliberately outside ComfyUI's live queue during a
    manual pause (see queue_hold_sync.py) — its absence from running/queued
    must not be mistaken for it having been cleared."""
    conn = init_db(str(tmp_path / "test.sqlite3"))
    state = AutopilotState()
    add_queue_item(conn, prompt_id="a", name="Held Job")
    set_queue_item_status(conn, prompt_id="a", status="held")

    sync_queue_tracker(conn, [], [], history={}, autopilot_state=state, seen_running=set(), seen_completed=set())

    assert {item["prompt_id"] for item in list_queue_items(conn)} == {"a"}


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
