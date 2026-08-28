"""In-memory registry of pending `wait_for_click` continues, keyed by prompt_id.

Bridges the synchronous node execution thread (which blocks in `wait_for_continue`)
and the aiohttp route handler that receives the click (`signal_continue`).
"""

import threading

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def wait_for_continue(prompt_id: str, poll_interval: float = 0.25) -> None:
    with _lock:
        event = _events.setdefault(prompt_id, threading.Event())
    try:
        event.wait()
    finally:
        with _lock:
            _events.pop(prompt_id, None)


def signal_continue(prompt_id: str) -> None:
    with _lock:
        event = _events.setdefault(prompt_id, threading.Event())
    event.set()
