import threading
import time

import pytest

from backend.continue_registry import (
    InterruptProcessingException,
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
