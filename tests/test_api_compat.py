import logging

from backend.api_compat import verify_prompt_queue_shape


class _FakeQueueGoodShape:
    def get_current_queue_volatile(self):
        return [], []

    def get_history(self):
        return {}


class _FakeQueueMissingMethod:
    def get_current_queue_volatile(self):
        return [], []
    # no get_history


class _FakeQueueRaises:
    def get_current_queue_volatile(self):
        raise TypeError("signature changed")

    def get_history(self):
        return {}


class _FakeQueueWrongShape:
    def get_current_queue_volatile(self):
        return "not-a-list", "also-not-a-list"

    def get_history(self):
        return {}


def test_returns_true_for_well_shaped_queue():
    assert verify_prompt_queue_shape(_FakeQueueGoodShape()) is True


def test_returns_false_and_warns_when_get_current_queue_volatile_missing(caplog):
    class _NoMethodAtAll:
        pass

    with caplog.at_level(logging.WARNING):
        result = verify_prompt_queue_shape(_NoMethodAtAll())
    assert result is False
    assert "get_current_queue_volatile" in caplog.text


def test_returns_false_and_warns_when_call_raises(caplog):
    with caplog.at_level(logging.WARNING):
        result = verify_prompt_queue_shape(_FakeQueueRaises())
    assert result is False
    assert "may have changed" in caplog.text


def test_returns_false_and_warns_on_unexpected_return_shape(caplog):
    with caplog.at_level(logging.WARNING):
        result = verify_prompt_queue_shape(_FakeQueueWrongShape())
    assert result is False
    assert "unexpected shape" in caplog.text


def test_returns_false_and_warns_when_get_history_missing(caplog):
    with caplog.at_level(logging.WARNING):
        result = verify_prompt_queue_shape(_FakeQueueMissingMethod())
    assert result is False
    assert "get_history" in caplog.text
