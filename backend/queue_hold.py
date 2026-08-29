"""Holds ComfyUI's own pending queue items during a manual pause, and releases
them back on resume.

The queue middleware (backend/queue_middleware.py) only ever gated new
POST /prompt submissions — it never reached into ComfyUI's own PromptQueue,
so jobs already submitted before a pause kept running regardless of pause
state. This module closes that gap for manual pause specifically: it moves
every not-yet-started item out of PromptQueue.queue into local storage, and
puts them back (in their original order) on resume.

Never touches PromptQueue.currently_running — a job that has already started
always finishes, matching the "never interrupt a running job" rule.
"""

from typing import Protocol


class _PromptQueueLike(Protocol):
    def get_current_queue_volatile(self) -> tuple[list, list]: ...
    def delete_queue_item(self, function) -> bool: ...
    def put(self, item) -> None: ...


class QueueHold:
    def __init__(self) -> None:
        self._held: list = []

    @property
    def has_held(self) -> bool:
        return len(self._held) > 0

    @property
    def items(self) -> list:
        """Read-only snapshot of what's currently held, in release order.
        Used to mirror state into SQLite (backend.persistence) so a crash or
        restart mid-pause doesn't lose in-flight jobs."""
        return list(self._held)

    def hold_pending(self, prompt_queue: _PromptQueueLike) -> int:
        """Remove every not-yet-running item from prompt_queue into local
        storage, in ascending `number` (priority) order. Returns how many
        items were actually held — an item that a worker already popped
        between the snapshot and the delete attempt is simply skipped."""
        _, queued = prompt_queue.get_current_queue_volatile()
        held_now = []
        for item in sorted(queued, key=lambda x: x[0]):
            prompt_id = item[1]
            if prompt_queue.delete_queue_item(lambda x, pid=prompt_id: x[1] == pid):
                held_now.append(item)
        self._held.extend(held_now)
        return len(held_now)

    def restore(self, items: list) -> None:
        """Re-populate held state from a persisted snapshot (backend.persistence
        held_items) without touching prompt_queue — used at startup when the
        held_items table is non-empty, which only happens while genuinely
        paused (it's cleared on every release), so this never fires unless a
        pause was in effect when the previous process stopped."""
        self._held = list(items)

    def release_held(self, prompt_queue: _PromptQueueLike) -> int:
        """Put every held item back into prompt_queue, in the order they
        were originally submitted."""
        released, self._held = self._held, []
        for item in released:
            prompt_queue.put(item)
        return len(released)
