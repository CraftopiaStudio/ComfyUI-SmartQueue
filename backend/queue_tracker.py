"""Mirrors ComfyUI's live prompt queue into Smart Queue's own persistence and
autopilot job counter.

Read-only against ComfyUI's own queue (get_current_queue_volatile / get_history)
so this never intercepts or delays /prompt itself, consistent with the
"non-intrusive" architecture (spec Section 5). Called on a periodic tick from
the autopilot background loop; any exception here must not affect that loop
(fail-open lives in the caller, same pattern as run_autopilot_tick).
"""

import json
from datetime import datetime
from urllib.parse import urlencode

from .autopilot_state import AutopilotState
from .persistence import (
    add_queue_item,
    list_queue_items,
    mark_completed,
    mark_queue_item_started,
    remove_queue_item,
)


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


def extract_thumbnail_query(entry: dict) -> str | None:
    """A /view-compatible query string for the first image ComfyUI's own
    history entry recorded, or None. Reuses ComfyUI's existing output file
    and /view endpoint instead of generating/storing a separate thumbnail —
    no extra disk cost for jobs that get cancelled or never viewed (spec §29
    §6)."""
    outputs = entry.get("outputs") if isinstance(entry, dict) else None
    if not isinstance(outputs, dict):
        return None
    for node_output in outputs.values():
        images = node_output.get("images") if isinstance(node_output, dict) else None
        if not images:
            continue
        image = images[0]
        filename = image.get("filename")
        if not filename:
            continue
        return urlencode({
            "filename": filename,
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        })
    return None


def extract_workflow_json(extra_data: dict) -> str | None:
    """The workflow graph embedded in the /prompt call, serialized for
    storage — lets a history thumbnail restore the exact workflow that
    produced it (spec §29 §6) without depending on PNG-embedded metadata."""
    try:
        workflow = extra_data.get("extra_pnginfo", {}).get("workflow")
    except AttributeError:
        return None
    if not workflow:
        return None
    return json.dumps(workflow)


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
            mark_queue_item_started(conn, prompt_id=prompt_id)
            seen_running = seen_running | {prompt_id}

    for prompt_id, entry in history.items():
        if prompt_id in seen_completed:
            continue
        history_item = entry.get("prompt") if isinstance(entry, dict) else None
        extra_data = history_item[3] if history_item and len(history_item) > 3 else {}
        if prompt_id not in known_ids:
            # Started and finished inside one tick gap: never seen running/
            # queued, so (deliberately) never gets a started_at either —
            # duration_seconds stays NULL for it downstream (spec §29 #3).
            add_queue_item(conn, prompt_id=prompt_id, name=extract_job_name(extra_data))
            if prompt_id not in seen_running:
                autopilot_state.record_job_started()
                seen_running = seen_running | {prompt_id}
        mark_completed(
            conn,
            prompt_id=prompt_id,
            thumbnail_path=extract_thumbnail_query(entry),
            workflow_json=extract_workflow_json(extra_data),
        )
        seen_completed = seen_completed | {prompt_id}

    # A pending row whose prompt_id is in none of running/queued/history was
    # removed from ComfyUI's live queue without ever executing — e.g. the
    # native "Clear Queue" button, which bypasses /smart_queue/cancel and so
    # never calls remove_queue_item itself. Without this, that row has no
    # other way to ever leave queue_items and sits in the panel's PENDING
    # list forever, surviving even a page refresh. A "held" row is exempt:
    # manual pause deliberately holds it outside ComfyUI's live queue (see
    # queue_hold_sync.py), so its absence from running/queued is expected,
    # not a sign it was cleared.
    live_ids = {item[1] for item in running} | {item[1] for item in queued} | set(history.keys())
    for row in list_queue_items(conn):
        if row["status"] != "held" and row["prompt_id"] not in live_ids:
            remove_queue_item(conn, prompt_id=row["prompt_id"])

    # Bound both sets to what ComfyUI can still show us, instead of growing
    # for the process lifetime (spec §26.2). A ended job is safe to forget
    # from seen_running the moment it drops out of the running list — by
    # then it's already in known_ids, so the history branch above can never
    # re-trigger record_job_started() for it regardless of this set. A
    # prompt_id is safe to forget from seen_completed once ComfyUI's own
    # history dict no longer holds it, since that's the only place it could
    # ever be seen again.
    seen_running = seen_running & {item[1] for item in running}
    seen_completed = seen_completed & set(history.keys())

    return seen_running, seen_completed
