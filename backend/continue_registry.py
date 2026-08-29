"""In-memory registry of pending `wait_for_click` continues, keyed by prompt_id.

Bridges the synchronous node execution thread (which blocks in `wait_for_continue`)
and the aiohttp route handlers that receive the click (`signal_continue`) or the
node-level cancel (`signal_cancel`).
"""

import threading

try:
    from comfy.model_management import (
        InterruptProcessingException,
        throw_exception_if_processing_interrupted,
    )
except ImportError:  # not running inside a real ComfyUI process (e.g. pytest)

    class InterruptProcessingException(Exception):  # type: ignore[no-redef]
        pass

    def throw_exception_if_processing_interrupted() -> None:  # type: ignore[no-redef]
        pass


_lock = threading.Lock()
_waits: dict[str, dict] = {}  # prompt_id -> {"event": threading.Event, "node_id": str | None}
_cancelled: set[str] = set()


def _get_or_create(prompt_id: str, node_id: str | None) -> threading.Event:
    entry = _waits.setdefault(prompt_id, {"event": threading.Event(), "node_id": node_id})
    if node_id is not None:
        entry["node_id"] = node_id
    return entry["event"]


def wait_for_continue(prompt_id: str, node_id: str | None = None, poll_interval: float = 0.25) -> None:
    with _lock:
        event = _get_or_create(prompt_id, node_id)
    try:
        # A bare event.wait() (no timeout) never gets a chance to notice
        # ComfyUI's own /interrupt, which just flips a global flag rather than
        # calling into this registry — only the node's own Cancel button
        # (signal_cancel, below) could unblock it. Polling with a short
        # timeout lets the native Cancel/interrupt button work too (spec §26.2).
        while not event.wait(timeout=poll_interval):
            throw_exception_if_processing_interrupted()
        with _lock:
            cancelled = prompt_id in _cancelled
        if cancelled:
            raise InterruptProcessingException()
    finally:
        with _lock:
            _waits.pop(prompt_id, None)
            _cancelled.discard(prompt_id)


def signal_continue(prompt_id: str) -> None:
    with _lock:
        event = _get_or_create(prompt_id, None)
    event.set()


def signal_cancel(prompt_id: str) -> None:
    with _lock:
        _cancelled.add(prompt_id)
        event = _get_or_create(prompt_id, None)
    event.set()


def list_pending() -> list[dict]:
    """Currently-blocked waits, as [{"prompt_id", "node_id"}, ...].

    Lets the frontend reconcile its Continue/Cancel button state against this
    ground truth after a page reload or a workflow-tab switch loses the
    one-shot `cooldown_wait_for_click` socket event (spec §26.2) — those never
    reach a tab that wasn't the active one at the moment the wait started, and
    a reload misses it outright since it already fired.

    Filters out entries whose event is already set: a stray `signal_continue`/
    `signal_cancel` on a prompt_id nothing is actually waiting on (or the brief
    window between a real signal and `wait_for_continue`'s own cleanup)
    would otherwise show up as a phantom pending wait.
    """
    with _lock:
        return [
            {"prompt_id": prompt_id, "node_id": entry["node_id"]}
            for prompt_id, entry in _waits.items()
            if not entry["event"].is_set()
        ]
