from backend.autopilot import Decision
from backend.autopilot_state import AutopilotState
from backend.persistence import init_db, load_held_items
from backend.queue_hold import QueueHold
from backend.queue_hold_sync import sync_queue_hold


class FakePromptQueue:
    def __init__(self, queue=None):
        self.queue = list(queue or [])
        self.put_calls = []

    def get_current_queue_volatile(self):
        return ([], list(self.queue))

    def delete_queue_item(self, function):
        for i, item in enumerate(self.queue):
            if function(item):
                self.queue.pop(i)
                return True
        return False

    def put(self, item):
        self.put_calls.append(item)
        self.queue.append(item)


def test_autopilot_pause_holds_already_queued_jobs():
    # This is the exact gap spec §26.2 recorded: the middleware only ever
    # gated new POST /prompt submissions, so jobs queued before an autopilot
    # pause kept executing regardless — the "20 jobs queued, nobody watching"
    # case the temperature/VRAM rules exist for.
    conn = init_db(":memory:")
    state = AutopilotState()
    queue_hold = QueueHold()
    prompt_queue = FakePromptQueue(queue=[(1.0, "a", {}, {}, [], {}), (2.0, "b", {}, {}, [], {})])

    state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
    transition, count = sync_queue_hold(conn, state, queue_hold, prompt_queue)

    assert transition == "held"
    assert count == 2
    assert prompt_queue.queue == []
    assert queue_hold.has_held is True
    assert [item[1] for item in load_held_items(conn)] == ["a", "b"]


def test_autopilot_resume_releases_held_jobs_back_into_prompt_queue():
    conn = init_db(":memory:")
    state = AutopilotState()
    queue_hold = QueueHold()
    prompt_queue = FakePromptQueue(queue=[(1.0, "a", {}, {}, [], {})])

    state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
    sync_queue_hold(conn, state, queue_hold, prompt_queue)

    state.apply(Decision(should_pause=False, reasons=()))
    transition, count = sync_queue_hold(conn, state, queue_hold, prompt_queue)

    assert transition == "released"
    assert count == 1
    assert [item[1] for item in prompt_queue.put_calls] == ["a"]
    assert queue_hold.has_held is False
    assert load_held_items(conn) == []


def test_no_transition_is_a_no_op():
    conn = init_db(":memory:")
    state = AutopilotState()
    queue_hold = QueueHold()
    prompt_queue = FakePromptQueue(queue=[(1.0, "a", {}, {}, [], {})])

    transition, count = sync_queue_hold(conn, state, queue_hold, prompt_queue)

    assert transition is None
    assert count == 0
    assert prompt_queue.queue == [(1.0, "a", {}, {}, [], {})]  # untouched


def test_flapping_vram_rule_does_not_repeatedly_hold_and_release():
    # Regression for the flapping risk spec §26.2 called out: without VRAM
    # hysteresis, should_pause could flip every 5s tick right at the
    # threshold, and each flip is an edge that would hold/release the live
    # queue. consume_effective_pause_transition() only fires once per actual
    # transition, so repeated identical Decisions must be no-ops.
    conn = init_db(":memory:")
    state = AutopilotState()
    queue_hold = QueueHold()
    prompt_queue = FakePromptQueue(queue=[(1.0, "a", {}, {}, [], {})])

    state.apply(Decision(should_pause=True, reasons=("low VRAM",)))
    first = sync_queue_hold(conn, state, queue_hold, prompt_queue)
    assert first == ("held", 1)

    # Same tick outcome repeated several times (as a level-triggered rule would
    # keep reporting while still above/below its own threshold).
    for _ in range(5):
        state.apply(Decision(should_pause=True, reasons=("low VRAM",)))
        assert sync_queue_hold(conn, state, queue_hold, prompt_queue) == (None, 0)

    assert prompt_queue.put_calls == []  # never released mid-pause
    assert queue_hold.has_held is True


def test_manual_and_autopilot_pause_overlap_does_not_release_early():
    # Manual pause holds the queue; autopilot also wants to pause, then
    # autopilot resumes on its own while manual pause is still on. The jobs
    # must stay held — releasing them here would defeat the still-active
    # manual pause.
    conn = init_db(":memory:")
    state = AutopilotState()
    queue_hold = QueueHold()
    prompt_queue = FakePromptQueue(queue=[(1.0, "a", {}, {}, [], {})])

    state.set_manual_pause(True)
    assert sync_queue_hold(conn, state, queue_hold, prompt_queue) == ("held", 1)

    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    assert sync_queue_hold(conn, state, queue_hold, prompt_queue) == (None, 0)

    state.apply(Decision(should_pause=False, reasons=()))
    assert sync_queue_hold(conn, state, queue_hold, prompt_queue) == (None, 0)
    assert queue_hold.has_held is True  # manual pause still holds it

    state.set_manual_pause(False)
    transition, count = sync_queue_hold(conn, state, queue_hold, prompt_queue)
    assert transition == "released"
    assert count == 1
