import threading
import time
from unittest.mock import patch

import pytest

from backend.continue_registry import (
    InterruptProcessingException,
    list_pending,
    signal_cancel,
    signal_continue,
    wait_for_continue,
)


def test_wait_for_continue_blocks_until_signaled():
    prompt_id = "abc123"
    result = {"returned": False}

    def waiter():
        wait_for_continue(prompt_id)
        result["returned"] = True

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    assert result["returned"] is False

    signal_continue(prompt_id)
    thread.join(timeout=2.0)
    assert result["returned"] is True


def test_signal_before_wait_does_not_deadlock():
    prompt_id = "signaled-early"
    signal_continue(prompt_id)

    result = {"returned": False}

    def waiter():
        wait_for_continue(prompt_id)
        result["returned"] = True

    thread = threading.Thread(target=waiter)
    thread.start()
    thread.join(timeout=2.0)
    assert result["returned"] is True


def test_signal_for_unknown_prompt_id_is_a_no_op():
    signal_continue("never-waited-on")


def test_signal_cancel_raises_interrupt_and_unblocks_waiter():
    prompt_id = "cancel-me"
    result = {"exc": None}

    def waiter():
        try:
            wait_for_continue(prompt_id)
        except InterruptProcessingException as exc:
            result["exc"] = exc

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)

    signal_cancel(prompt_id)
    thread.join(timeout=2.0)

    assert isinstance(result["exc"], InterruptProcessingException)


def test_cancel_flag_does_not_leak_to_the_next_wait_on_the_same_id():
    prompt_id = "reused-id"
    signal_cancel(prompt_id)
    with pytest.raises(InterruptProcessingException):
        wait_for_continue(prompt_id)

    # A later, unrelated wait on the same prompt_id must not still be "cancelled".
    signal_continue(prompt_id)
    wait_for_continue(prompt_id)  # should return normally, not raise


def test_native_interrupt_frees_a_waiter_with_no_click(monkeypatch):
    """ComfyUI's own /interrupt sets model_management's global flag rather than
    calling into this registry at all. Before this fix, wait_for_continue's bare
    event.wait() with no timeout never got a chance to notice — only the node's
    own Cancel button (signal_cancel) could unblock it (spec §26.2)."""
    prompt_id = "native-interrupt"
    interrupted = {"flag": False}

    def fake_throw():
        if interrupted["flag"]:
            raise InterruptProcessingException()

    monkeypatch.setattr(
        "backend.continue_registry.throw_exception_if_processing_interrupted", fake_throw
    )

    result = {"exc": None}

    def waiter():
        try:
            wait_for_continue(prompt_id, poll_interval=0.05)
        except InterruptProcessingException as exc:
            result["exc"] = exc

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    assert result["exc"] is None  # still parked, nobody has clicked anything

    interrupted["flag"] = True
    thread.join(timeout=2.0)
    assert isinstance(result["exc"], InterruptProcessingException)


def test_list_pending_reports_node_id_for_an_active_wait():
    prompt_id = "with-node"
    thread = threading.Thread(target=wait_for_continue, args=(prompt_id,), kwargs={"node_id": "42"})
    thread.start()
    time.sleep(0.1)
    try:
        assert {"prompt_id": prompt_id, "node_id": "42"} in list_pending()
    finally:
        signal_continue(prompt_id)
        thread.join(timeout=2.0)


def test_list_pending_omits_waits_that_already_resolved():
    prompt_id = "long-gone"
    signal_continue(prompt_id)  # no one ever waited on it — must not linger as "pending"
    assert all(item["prompt_id"] != prompt_id for item in list_pending())


def test_list_pending_is_empty_with_nothing_waiting():
    assert list_pending() == []
