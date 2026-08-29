"""Mirrors ComfyUI's live prompt queue into Smart Queue's own persistence and
autopilot job counter.

Read-only against ComfyUI's own queue (get_current_queue_volatile / get_history)
so this never intercepts or delays /prompt itself, consistent with the
"non-intrusive" architecture (spec Section 5). Called on a periodic tick from
the autopilot background loop; any exception here must not affect that loop
(fail-open lives in the caller, same pattern as run_autopilot_tick).
"""

from datetime import datetime

from .autopilot_state import AutopilotState
from .persistence import add_queue_item, list_queue_items, mark_completed


def extract_job_name(extra_data: dict) -> str:
    """Best-effort name from the workflow metadata ComfyUI already sends with
    every /prompt call. Falls back to a timestamp if nothing usable is found."""
    try:
        workflow = extra_data.get("extra_pnginfo", {}).get("workflow", {}) or {}
        extra = workflow.get("extra")
        name = extra.get("workflow_name") if isinstance(extra, dict) else None
        if not name:
            name = workflow.get("name")
        if name:
            return str(name)
    except AttributeError:
        pass
    return datetime.now().strftime("Job %Y-%m-%d %H:%M:%S")


def sync_queue_tracker(
    conn,
    running: list,
    queued: list,
    history: dict,
    autopilot_state: AutopilotState,
    seen_running: set,
    seen_completed: set,
) -> tuple[set, set]:
    """One sync tick. Returns the updated (seen_running, seen_completed) sets.

    seen_running: prompt_ids already counted via record_job_started(), so a
    job still running on the next tick isn't double-counted.
    seen_completed: prompt_ids already written to the history table, so a
    prompt_id lingering in ComfyUI's in-memory history isn't re-processed
    every tick. Also covers jobs that start and finish inside a single tick
    gap (never observed as running/queued) by adding them retroactively from
    the history entry's own metadata before marking them complete.
    """
    known_ids = {item["prompt_id"] for item in list_queue_items(conn)}

    for item in running + queued:
        prompt_id = item[1]
        extra_data = item[3] if len(item) > 3 else {}
        if prompt_id not in known_ids and prompt_id not in seen_completed:
            add_queue_item(conn, prompt_id=prompt_id, name=extract_job_name(extra_data))
            known_ids = known_ids | {prompt_id}

    for item in running:
        prompt_id = item[1]
        if prompt_id not in seen_running:
            autopilot_state.record_job_started()
            seen_running = seen_running | {prompt_id}

    for prompt_id, entry in history.items():
        if prompt_id in seen_completed:
            continue
        if prompt_id not in known_ids:
            # Started and finished inside one tick gap: never seen running/queued.
            history_item = entry.get("prompt") if isinstance(entry, dict) else None
            extra_data = history_item[3] if history_item and len(history_item) > 3 else {}
            add_queue_item(conn, prompt_id=prompt_id, name=extract_job_name(extra_data))
            if prompt_id not in seen_running:
                autopilot_state.record_job_started()
                seen_running = seen_running | {prompt_id}
        mark_completed(conn, prompt_id=prompt_id)
        seen_completed = seen_completed | {prompt_id}

    return seen_running, seen_completed
