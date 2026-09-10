"""resolve_prompt_id answers from the first source that can, and answers None
rather than raising when none can — it runs inside node execution, where an
exception fails the user's whole prompt.

The source order mirrors ComfyUI's own progress hook (main.py:436-445):
execution context first, then the server's last_prompt_id, then the running
queue. last_prompt_id is real (main.py:364 sets it) but undeclared in
PromptServer.__init__, so it is a fallback rather than the primary source.
"""

from backend.prompt_context import resolve_prompt_id


class _FakeContext:
    def __init__(self, prompt_id):
        self.prompt_id = prompt_id


class _FakeServer:
    def __init__(self, last_prompt_id):
        self.last_prompt_id = last_prompt_id


class _FakeQueue:
    def __init__(self, running):
        self._running = running

    def get_current_queue_volatile(self):
        return self._running, []


def _queue():
    return _FakeQueue([(3, "from-queue", {}, {}, [], {})])


def test_prefers_the_execution_context_over_everything_else():
    resolved = resolve_prompt_id(
        lambda: _FakeContext("from-context"), _FakeServer("from-server"), _queue()
    )
    assert resolved == "from-context"


def test_falls_back_to_the_server_attribute_when_context_is_none():
    assert resolve_prompt_id(lambda: None, _FakeServer("from-server"), _queue()) == "from-server"


def test_falls_back_to_the_server_attribute_when_context_raises():
    def _boom():
        raise RuntimeError("comfy_execution changed shape")

    assert resolve_prompt_id(_boom, _FakeServer("from-server"), _queue()) == "from-server"


def test_falls_back_to_the_running_queue_when_the_server_attribute_is_absent():
    class _ServerWithoutIt:
        pass

    assert resolve_prompt_id(lambda: None, _ServerWithoutIt(), _queue()) == "from-queue"


def test_returns_none_when_nothing_is_running():
    assert resolve_prompt_id(lambda: None, None, _FakeQueue([])) is None


def test_returns_none_when_the_queue_raises():
    class _BrokenQueue:
        def get_current_queue_volatile(self):
            raise AttributeError("PromptQueue changed shape")

    assert resolve_prompt_id(lambda: None, None, _BrokenQueue()) is None


def test_returns_none_when_no_source_is_available():
    assert resolve_prompt_id(None, None, None) is None


def test_ignores_a_context_with_an_empty_prompt_id():
    assert resolve_prompt_id(lambda: _FakeContext(""), None, _queue()) == "from-queue"


def test_returns_none_when_the_running_item_has_an_invalid_shape():
    # If the queue's running item lacks a [1] index (e.g. a bare one-element
    # tuple), extraction should fail gracefully and return None, not raise.
    class _QueueWithInvalidShape:
        def get_current_queue_volatile(self):
            return (("single-element-tuple",),), []

    assert resolve_prompt_id(lambda: None, None, _QueueWithInvalidShape()) is None
