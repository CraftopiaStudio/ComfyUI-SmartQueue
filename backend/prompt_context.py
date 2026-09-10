"""Resolves the prompt_id of the prompt currently being executed.

Source order mirrors ComfyUI's own progress hook (main.py:436-445): the
documented per-node execution context first, then PromptServer's
last_prompt_id, then the running-queue snapshot.

last_prompt_id does work — main.py:364 assigns it just before e.execute() —
but PromptServer.__init__ never declares it (unlike last_node_id and
client_id, server.py:264-265), so it exists only because one line in
main.py's worker loop happens to set it. Anything embedding ComfyUI without
that loop would not have it. Hence: fallback, not primary.

The running-queue snapshot is last and is unambiguous because the worker
executes one prompt at a time.
"""

from typing import Callable


def resolve_prompt_id(
    context_fn: Callable[[], object] | None = None,
    server=None,
    prompt_queue=None,
) -> str | None:
    """Best-effort current prompt_id, or None when no source can answer.

    Never raises: this runs inside node execution, where an exception fails
    the user's whole prompt. A None result means "skip the wait", which the
    caller reports in the node's status output.
    """
    if context_fn is not None:
        try:
            context = context_fn()
        except Exception:
            context = None
        prompt_id = getattr(context, "prompt_id", None)
        if prompt_id:
            return prompt_id

    if server is not None:
        prompt_id = getattr(server, "last_prompt_id", None)
        if prompt_id:
            return prompt_id

    if prompt_queue is not None:
        try:
            running, _queued = prompt_queue.get_current_queue_volatile()
            if running:
                return running[0][1]
        except Exception:
            pass

    return None
