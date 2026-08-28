"""Smoke test: the package's __init__.py loads cleanly and registers the node."""

import importlib.util
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _load_init_module():
    spec = importlib.util.spec_from_file_location(
        "smart_queue_init",
        PACKAGE_ROOT / "__init__.py",
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    # Relative imports inside __init__.py (e.g. `from .backend...`) need this
    # module registered in sys.modules before exec, or Python can't resolve
    # "smart_queue_init" as their parent package.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_init_exposes_node_mappings():
    module = _load_init_module()
    assert isinstance(module.NODE_CLASS_MAPPINGS, dict)
    assert isinstance(module.NODE_DISPLAY_NAME_MAPPINGS, dict)


def test_cooldown_node_registered_under_backward_compatible_id():
    module = _load_init_module()
    assert "RubzGpuCooldownNode" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS["RubzGpuCooldownNode"] == "Smart Cooldown & Pause"


def test_web_directory_is_declared():
    module = _load_init_module()
    assert module.WEB_DIRECTORY == "./web"
