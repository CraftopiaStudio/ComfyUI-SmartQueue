from backend.queue_hold import (
    QueueHold,
    cancel_queue_item,
    reorder_pending_queue,
    requeue_item_at_back,
)


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


def test_reorder_held_rearranges_release_order():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b"), make_item(3, "c")])
    hold = QueueHold()
    hold.hold_pending(pq)

    count = hold.reorder_held(["c", "a", "b"])

    assert count == 3
    released = hold.release_held(pq)
    assert released == 3
    # Must check by actual number, not put()-call order: PromptQueue.queue is
    # a heap ordered by item[0], so only the numbers themselves determine
    # execution order.
    put_ids_by_number = [item[1] for item in sorted(pq.put_calls, key=lambda x: x[0])]
    assert put_ids_by_number == ["c", "a", "b"]


def test_reorder_held_appends_unlisted_items_after_named_ones():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b"), make_item(3, "c")])
    hold = QueueHold()
    hold.hold_pending(pq)

    hold.reorder_held(["b"])

    assert [item[1] for item in hold.items] == ["b", "a", "c"]


def test_reorder_held_on_empty_hold_returns_zero():
    hold = QueueHold()
    assert hold.reorder_held(["a", "b"]) == 0


def test_cancel_held_removes_the_item_without_touching_prompt_queue():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b")])
    hold = QueueHold()
    hold.hold_pending(pq)

    removed = hold.cancel_held("a")

    assert removed[1] == "a"
    assert [item[1] for item in hold.items] == ["b"]
    assert pq.put_calls == []


def test_cancel_held_returns_none_for_an_unheld_id():
    hold = QueueHold()
    hold.restore([(1.0, "a", {}, {}, [], {})])
    assert hold.cancel_held("nope") is None
    assert hold.has_held is True


def test_requeue_held_at_back_moves_item_to_the_end_without_releasing_it():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b"), make_item(3, "c")])
    hold = QueueHold()
    hold.hold_pending(pq)

    moved = hold.requeue_held_at_back("a")

    assert moved is True
    assert [item[1] for item in hold.items] == ["b", "c", "a"]
    assert pq.put_calls == []


def test_requeue_held_at_back_returns_false_for_an_unheld_id():
    hold = QueueHold()
    hold.restore([(1.0, "a", {}, {}, [], {})])
    assert hold.requeue_held_at_back("nope") is False


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


def test_cancel_queue_item_removes_and_returns_it():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b")])
    removed = cancel_queue_item(pq, "a")
    assert removed[1] == "a"
    assert [item[1] for item in pq.queue] == ["b"]


def test_cancel_queue_item_returns_none_when_not_pending():
    pq = FakePromptQueue(running=[make_item(0, "running")], queue=[])
    assert cancel_queue_item(pq, "running") is None
    assert cancel_queue_item(pq, "ghost") is None


def test_requeue_item_at_back_gets_a_higher_number_than_everything_queued():
    pq = FakePromptQueue(queue=[make_item(5, "a"), make_item(9, "b")])
    item = make_item(1, "c")
    requeue_item_at_back(pq, item)
    put_item = pq.put_calls[0]
    assert put_item[1] == "c"
    assert put_item[0] > 9


def test_requeue_item_at_back_on_empty_queue_keeps_its_own_number():
    pq = FakePromptQueue(queue=[])
    item = make_item(3, "solo")
    requeue_item_at_back(pq, item)
    assert pq.put_calls[0][0] == 3


def test_reorder_pending_queue_renumbers_to_match_requested_order():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b"), make_item(3, "c")])
    count = reorder_pending_queue(pq, ["c", "a", "b"])
    assert count == 3
    final_ids_by_number = sorted(pq.queue, key=lambda x: x[0])
    assert [item[1] for item in final_ids_by_number] == ["c", "a", "b"]


def test_reorder_pending_queue_appends_unlisted_items_after_named_ones():
    pq = FakePromptQueue(queue=[make_item(1, "a"), make_item(2, "b"), make_item(3, "c")])
    reorder_pending_queue(pq, ["b"])
    final_ids_by_number = sorted(pq.queue, key=lambda x: x[0])
    assert [item[1] for item in final_ids_by_number] == ["b", "a", "c"]


def test_reorder_pending_queue_on_empty_queue_returns_zero():
    pq = FakePromptQueue(queue=[])
    assert reorder_pending_queue(pq, ["a", "b"]) == 0
