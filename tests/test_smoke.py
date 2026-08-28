"""Smoke test: the package's __init__.py loads cleanly outside a running ComfyUI instance."""

import importlib.util
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def test_init_exposes_node_mappings():
    spec = importlib.util.spec_from_file_location("smart_queue_init", PACKAGE_ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert isinstance(module.NODE_CLASS_MAPPINGS, dict)
    assert isinstance(module.NODE_DISPLAY_NAME_MAPPINGS, dict)
