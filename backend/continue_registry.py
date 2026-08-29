"""In-memory registry of pending `wait_for_click` continues, keyed by prompt_id.

Bridges the synchronous node execution thread (which blocks in `wait_for_continue`)
and the aiohttp route handlers that receive the click (`signal_continue`) or the
node-level cancel (`signal_cancel`).
"""

import threading

try:
    from comfy.model_management import InterruptProcessingException
except ImportError:  # not running inside a real ComfyUI process (e.g. pytest)

    class InterruptProcessingException(Exception):  # type: ignore[no-redef]
        pass


_lock = threading.Lock()
_events: dict[str, threading.Event] = {}
_cancelled: set[str] = set()


def wait_for_continue(prompt_id: str, poll_interval: float = 0.25) -> None:
    with _lock:
        event = _events.setdefault(prompt_id, threading.Event())
    try:
        event.wait()
        with _lock:
            cancelled = prompt_id in _cancelled
        if cancelled:
            raise InterruptProcessingException()
    finally:
        with _lock:
            _events.pop(prompt_id, None)
            _cancelled.discard(prompt_id)


def signal_continue(prompt_id: str) -> None:
    with _lock:
        event = _events.setdefault(prompt_id, threading.Event())
    event.set()


def signal_cancel(prompt_id: str) -> None:
    with _lock:
        _cancelled.add(prompt_id)
        event = _events.setdefault(prompt_id, threading.Event())
    event.set()
