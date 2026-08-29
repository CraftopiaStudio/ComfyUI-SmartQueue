"""Smoke test: the package's __init__.py loads cleanly and registers the node."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
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


def test_cooldown_node_registered_with_display_name():
    module = _load_init_module()
    assert "SmartCooldownNode" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS["SmartCooldownNode"] == "Smart Cooldown & Pause"


def test_web_directory_is_declared():
    module = _load_init_module()
    assert module.WEB_DIRECTORY == "./web"


def test_history_cleanup_runs_immediately_when_never_run_before():
    module = _load_init_module()
    now = datetime.now(timezone.utc)
    assert module._should_run_history_cleanup(now, None, retention_days=30) is True


def test_history_cleanup_is_throttled_within_the_hour():
    module = _load_init_module()
    now = datetime.now(timezone.utc)
    last_cleanup = now - timedelta(minutes=30)
    assert module._should_run_history_cleanup(now, last_cleanup, retention_days=30) is False


def test_history_cleanup_runs_again_after_an_hour():
    module = _load_init_module()
    now = datetime.now(timezone.utc)
    last_cleanup = now - timedelta(hours=1, seconds=1)
    assert module._should_run_history_cleanup(now, last_cleanup, retention_days=30) is True


def test_history_cleanup_never_runs_when_retention_is_disabled():
    module = _load_init_module()
    now = datetime.now(timezone.utc)
    assert module._should_run_history_cleanup(now, None, retention_days=0) is False
