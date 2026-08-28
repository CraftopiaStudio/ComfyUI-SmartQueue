import threading
import time

from backend.continue_registry import wait_for_continue, signal_continue


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
