"""The node must survive a backend that cannot start.

ComfyUI loads a pack by exec'ing its __init__.py (nodes.py). An exception
there is caught only by the outer handler, which abandons the whole module
before NODE_CLASS_MAPPINGS is ever read — so an unguarded import-time side
effect turns "autopilot is unavailable" into "the node does not exist".
The real integration needs a running ComfyUI, so this asserts the structure
instead: mappings first, every side effect inside a try/except Exception.
"""

import ast
from pathlib import Path

INIT_PY = Path(__file__).resolve().parent.parent / "__init__.py"


def _module():
    return ast.parse(INIT_PY.read_text(encoding="utf-8"))


def _server_integration_block(module):
    for node in module.body:
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "_HAS_COMFY_SERVER":
            return node
    raise AssertionError("no module-level `if _HAS_COMFY_SERVER:` block found")


def test_server_integration_is_wrapped_in_try_except():
    block = _server_integration_block(_module())
    assert len(block.body) == 1, "the whole block must be a single guarded statement"
    guarded = block.body[0]
    assert isinstance(guarded, ast.Try), "server integration must run inside try/except"
    caught = [
        handler.type.id
        for handler in guarded.handlers
        if isinstance(handler.type, ast.Name)
    ]
    assert "Exception" in caught


def test_node_mappings_are_assigned_before_the_server_integration():
    module = _module()
    block = _server_integration_block(module)
    mapping_lines = [
        node.lineno
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "NODE_CLASS_MAPPINGS"
    ]
    assert mapping_lines, "NODE_CLASS_MAPPINGS is not assigned at module level"
    assert max(mapping_lines) < block.lineno
