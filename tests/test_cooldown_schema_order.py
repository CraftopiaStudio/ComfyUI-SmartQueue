"""Pins the declared order (and required/optional split) of SmartCooldownNode's
input widgets.

comfy_api isn't importable outside a real ComfyUI process (see conftest.py /
_HAS_COMFY_IO in backend/nodes/cooldown.py), so define_schema() itself can't
be called here. Instead this parses the source of cooldown.py with `ast` and
checks the literal io.X.Input(...) calls inside define_schema()'s `inputs=`
list — the exact thing the "FROZEN ORDER" / widget-order comments in that
file warn not to touch by hand. A silent reorder there desyncs Nodes 2.0's
positional default-value assignment and corrupts values on unrelated widgets
(this already happened once, per the comment above `inputs=[` in
cooldown.py) — this test turns that into a loud, specific pytest failure
instead of a live-only symptom.
"""

import ast
from pathlib import Path

COOLDOWN_PY = Path(__file__).resolve().parent.parent / "backend" / "nodes" / "cooldown.py"

# (widget name, is_optional) in the exact order they must be declared in
# define_schema()'s `inputs=` list. Update this list deliberately (and bump
# a CHANGELOG entry, since this list is link-breaking for saved workflows)
# if the schema intentionally changes — never "fix" the test by copying
# whatever the code currently does without checking why it moved.
EXPECTED_INPUT_ORDER = [
    ("passthrough", True),
    ("passthrough_2", True),
    ("fixed_delay_seconds", False),
    ("wait_for_temp", False),
    ("target_temp_c", False),
    ("poll_interval_seconds", False),
    ("max_wait_seconds", False),
    ("notify_toast", False),
    ("notify_sound", False),
    ("notify_sound_choice", False),
    ("custom_sound_path", False),
    ("unload_models_before_wait", False),
    ("clear_cache_before_wait", False),
    ("wait_for_click", False),
]

EXPECTED_OUTPUT_ORDER = ["passthrough", "passthrough_2", "status"]


def _find_call(node: ast.AST, attr_name: str) -> ast.Call:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == attr_name
        ):
            return child
    raise AssertionError(f"Could not find a {attr_name}(...) call in {COOLDOWN_PY}")


def _keyword_value(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _input_entry(call: ast.Call) -> tuple[str, bool]:
    # e.g. io.Float.Input("fixed_delay_seconds", default=30.0, ...)
    name = call.args[0].value
    optional_node = _keyword_value(call, "optional")
    is_optional = bool(optional_node.value) if isinstance(optional_node, ast.Constant) else False
    return (name, is_optional)


def _output_name(call: ast.Call) -> str:
    return call.args[0].value


def _parse_schema_calls():
    tree = ast.parse(COOLDOWN_PY.read_text(encoding="utf-8"))
    define_schema = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "define_schema":
            define_schema = node
            break
    assert define_schema is not None, "define_schema() not found in cooldown.py"

    schema_call = _find_call(define_schema, "Schema")
    inputs_list = _keyword_value(schema_call, "inputs")
    outputs_list = _keyword_value(schema_call, "outputs")
    assert isinstance(inputs_list, ast.List)
    assert isinstance(outputs_list, ast.List)

    inputs = [_input_entry(elt) for elt in inputs_list.elts if isinstance(elt, ast.Call)]
    outputs = [_output_name(elt) for elt in outputs_list.elts if isinstance(elt, ast.Call)]
    return inputs, outputs


def test_input_widget_declaration_order_is_unchanged():
    inputs, _outputs = _parse_schema_calls()
    assert inputs == EXPECTED_INPUT_ORDER, (
        "SmartCooldownNode's define_schema() input order/optionality changed. "
        "This desyncs Nodes 2.0's positional default-value assignment and can "
        "corrupt values on unrelated widgets in every saved workflow using this "
        "node. If this is a deliberate schema change, update EXPECTED_INPUT_ORDER "
        "here deliberately and note it in CHANGELOG.md."
    )


def test_output_socket_order_is_frozen():
    _inputs, outputs = _parse_schema_calls()
    assert outputs == EXPECTED_OUTPUT_ORDER, (
        "SmartCooldownNode's output socket order changed. ComfyUI links outputs "
        "by positional index, not name, so this silently repoints every link in "
        "every saved workflow using this node — see the FROZEN ORDER comment "
        "above `outputs=` in backend/nodes/cooldown.py."
    )
