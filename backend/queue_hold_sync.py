"""Bridges an AutopilotState pause/resume transition to QueueHold + SQLite.

Used by both POST /smart_queue/manual_pause and the autopilot background
loop, so either source of a pause holds/releases already-queued jobs the
same way — sharing AutopilotState.consume_effective_pause_transition()'s
single edge-trigger keeps two overlapping pauses (manual + autopilot at
once) from double-holding or releasing too early (spec §26.2).
"""

from .autopilot_state import AutopilotState
from .persistence import add_queue_item, list_queue_items, save_held_items, set_queue_item_status
from .queue_hold import QueueHold
from .queue_tracker import extract_job_name


def sync_queue_hold(conn, state: AutopilotState, queue_hold: QueueHold, prompt_queue) -> tuple[str | None, int]:
    """Returns (None, 0) when effective_paused didn't just transition, or
    ("held"/"released", count) when this call is the one that acted on it —
    the caller (a route handler reporting counts, or the autopilot loop
    logging one) never needs its own was_paused/now_paused bookkeeping."""
    transition = state.consume_effective_pause_transition()
    if transition is None:
        return None, 0

    if transition == "held":
        count = queue_hold.hold_pending(prompt_queue)
        save_held_items(conn, queue_hold.items)
        # A held item may never have been synced into queue_items by the
        # periodic queue_tracker tick — e.g. paused within the same tick
        # window it was submitted in. Without this it silently drops out of
        # the panel's list even though it's safely held.
        known_ids = {row["prompt_id"] for row in list_queue_items(conn)}
        for item in queue_hold.items:
            prompt_id = item[1]
            if prompt_id not in known_ids:
                extra_data = item[3] if len(item) > 3 else {}
                add_queue_item(conn, prompt_id=prompt_id, name=extract_job_name(extra_data))
            set_queue_item_status(conn, prompt_id=prompt_id, status="held")
        return "held", count

    held_snapshot = queue_hold.items
    count = queue_hold.release_held(prompt_queue)
    save_held_items(conn, queue_hold.items)
    for item in held_snapshot:
        set_queue_item_status(conn, prompt_id=item[1], status="pending")
    return "released", count
