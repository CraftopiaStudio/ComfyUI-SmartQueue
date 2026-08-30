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
        """Put every held item back into prompt_queue, in _held's current
        order (ascending original number unless reorder_held changed it).

        Renumbers each item starting from the lowest original number rather
        than re-`put`-ting the original tuples unchanged: PromptQueue.queue
        is a heap ordered strictly by item[0], not by put() call order, so
        without renumbering, a reorder_held() reordering would be silently
        ignored on release — the heap would still execute items by their
        old numbers, i.e. original submission order."""
        released, self._held = self._held, []
        if not released:
            return 0
        base_number = min(item[0] for item in released)
        for offset, item in enumerate(released):
            prompt_queue.put((base_number + offset,) + tuple(item[1:]))
        return len(released)

    def cancel_held(self, prompt_id: str) -> tuple | None:
        """Removes prompt_id from held storage outright, without touching
        prompt_queue. Held items are deliberately skipped by the live-queue
        cancel path (manual pause owns them, per §14), which used to leave
        Cancel on a held row silently doing nothing (spec §26.2) — this lets
        a paused job actually be pruned instead of only ever released."""
        for i, item in enumerate(self._held):
            if item[1] == prompt_id:
                return self._held.pop(i)
        return None

    def requeue_held_at_back(self, prompt_id: str) -> bool:
        """Moves a held item to the back of the held list, mirroring
        requeue_item_at_back's "cancel and move to the back" for a job that
        hasn't been released into prompt_queue yet. Stays inside QueueHold
        rather than putting the item into the live queue, which would let it
        run immediately and defeat the pause it's being held under."""
        for i, item in enumerate(self._held):
            if item[1] == prompt_id:
                self._held.append(self._held.pop(i))
                return True
        return False

    def reorder_held(self, ordered_prompt_ids: list[str]) -> int:
        """Rearranges the held items to release in ordered_prompt_ids order.
        Without this, dragging a held item in the panel only changed its
        SQLite display order — release_held would still put items back in
        their original captured order, so a reorder made while paused was
        silently discarded on resume. Any held prompt_id absent from
        ordered_prompt_ids keeps its relative position, appended after the
        named ones (mirrors reorder_pending_queue's leftover handling)."""
        if not self._held:
            return 0
        by_id = {item[1]: item for item in self._held}
        named = [by_id[pid] for pid in ordered_prompt_ids if pid in by_id]
        named_ids = {item[1] for item in named}
        leftovers = [item for item in self._held if item[1] not in named_ids]
        self._held = named + leftovers
        return len(named)


def cancel_queue_item(prompt_queue: _PromptQueueLike, prompt_id: str) -> tuple | None:
    """Removes prompt_id from the pending queue if present. Never touches a
    currently-running job — delete_queue_item only searches PromptQueue.queue,
    which excludes currently_running by construction."""
    result: list = []

    def _match(item):
        if item[1] == prompt_id:
            result.append(item)
            return True
        return False

    if prompt_queue.delete_queue_item(_match):
        return result[0]
    return None


def requeue_item_at_back(prompt_queue: _PromptQueueLike, item: tuple) -> None:
    """Re-inserts item with a number higher than every currently-queued item,
    so it executes last. PromptQueue.queue is heap-ordered by item[0] — simply
    calling .put() with the item's original number would NOT move it to the
    back, since the heap doesn't care about insertion order."""
    _, queued = prompt_queue.get_current_queue_volatile()
    max_number = max((existing[0] for existing in queued), default=item[0] - 1)
    new_number = max(max_number + 1, item[0])
    prompt_queue.put((new_number,) + tuple(item[1:]))


def reorder_pending_queue(prompt_queue: _PromptQueueLike, ordered_prompt_ids: list[str]) -> int:
    """Renumbers every pending item so PromptQueue's heap executes them in
    ordered_prompt_ids order. Removing and re-`put`-ting a tuple unchanged
    would NOT do this — the heap orders strictly by item[0], not by put()
    call order — so each item gets a fresh, strictly increasing number
    starting from the lowest number currently in the queue.

    Any queued prompt_id absent from ordered_prompt_ids keeps its relative
    order among the leftovers and is appended after the named ones, rather
    than being dropped or racing to the front."""
    _, queued = prompt_queue.get_current_queue_volatile()
    if not queued:
        return 0

    queued_sorted = sorted(queued, key=lambda x: x[0])
    base_number = queued_sorted[0][0]
    by_id = {item[1]: item for item in queued_sorted}

    named = [by_id[pid] for pid in ordered_prompt_ids if pid in by_id]
    named_ids = {item[1] for item in named}
    leftovers = [item for item in queued_sorted if item[1] not in named_ids]
    target_order = named + leftovers

    touched = 0
    for offset, item in enumerate(target_order):
        prompt_id = item[1]
        if prompt_queue.delete_queue_item(lambda x, pid=prompt_id: x[1] == pid):
            prompt_queue.put((base_number + offset,) + tuple(item[1:]))
            touched += 1
    return touched
