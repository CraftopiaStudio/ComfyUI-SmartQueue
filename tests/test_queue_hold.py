from backend.queue_hold import QueueHold


class FakePromptQueue:
    """Duck-types the subset of ComfyUI's PromptQueue that queue_hold needs.

    `queue` mimics the heap's internal list — deliberately NOT stored in
    number order, since PromptQueue.queue is a heap array (heap-ordered,
    not sorted) and get_current_queue_volatile() returns a shallow copy of
    it as-is.
    """

    def __init__(self, running=None, queue=None):
        self.running = running or []
        self.queue = list(queue or [])
        self.put_calls = []

    def get_current_queue_volatile(self):
        return (list(self.running), list(self.queue))

    def delete_queue_item(self, function):
        for i, item in enumerate(self.queue):
            if function(item):
                self.queue.pop(i)
                return True
        return False

    def put(self, item):
        self.put_calls.append(item)
        self.queue.append(item)


def make_item(number, prompt_id):
    return (number, prompt_id, {"fake": "prompt"}, {}, [], {})


def test_hold_pending_removes_all_queued_items():
    pq = FakePromptQueue(queue=[make_item(2, "b"), make_item(1, "a")])
    hold = QueueHold()

    count = hold.hold_pending(pq)

    assert count == 2
    assert pq.queue == []
    assert hold.has_held is True


def test_hold_pending_does_not_touch_currently_running():
    running_item = make_item(0, "running-job")
    pq = FakePromptQueue(running=[running_item], queue=[make_item(1, "a")])
    hold = QueueHold()

    hold.hold_pending(pq)

    assert pq.running == [running_item]


def test_hold_pending_returns_zero_when_queue_empty():
    pq = FakePromptQueue(queue=[])
    hold = QueueHold()

    assert hold.hold_pending(pq) == 0
    assert hold.has_held is False


def test_release_held_puts_items_back_in_original_number_order():
    # Heap array intentionally out of number order (2 before 1) — release
    # must still restore ascending number order, not the heap's raw order.
    pq = FakePromptQueue(queue=[make_item(2, "b"), make_item(1, "a")])
    hold = QueueHold()
    hold.hold_pending(pq)

    released = hold.release_held(pq)

    assert released == 2
    assert [item[1] for item in pq.put_calls] == ["a", "b"]
    assert hold.has_held is False


def test_release_held_is_noop_when_nothing_held():
    pq = FakePromptQueue(queue=[])
    hold = QueueHold()

    assert hold.release_held(pq) == 0
    assert pq.put_calls == []


def test_items_property_reflects_held_snapshot_in_order():
    pq = FakePromptQueue(queue=[make_item(2, "b"), make_item(1, "a")])
    hold = QueueHold()

    hold.hold_pending(pq)

    assert [item[1] for item in hold.items] == ["a", "b"]


def test_restore_sets_held_items_without_touching_prompt_queue():
    pq = FakePromptQueue(queue=[])
    hold = QueueHold()

    hold.restore([make_item(1, "a"), make_item(2, "b")])

    assert hold.has_held is True
    assert [item[1] for item in hold.items] == ["a", "b"]
    assert pq.put_calls == []
    assert pq.queue == []


def test_hold_pending_skips_item_that_vanishes_before_delete():
    class VanishingQueue(FakePromptQueue):
        def delete_queue_item(self, function):
            # Simulate a worker popping the item between snapshot and delete.
            return False

    pq = VanishingQueue(queue=[make_item(1, "a")])
    hold = QueueHold()

    count = hold.hold_pending(pq)

    assert count == 0
    assert hold.has_held is False
