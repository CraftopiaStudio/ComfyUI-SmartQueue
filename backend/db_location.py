"""Resolves where smart_queue.sqlite3 lives.

Prefers ComfyUI's per-extension user directory (folder_paths.get_system_user_directory)
over the extension's own folder, so state survives a git pull/update and isn't
written into what may be a read-only or version-controlled install path. Falls
back to the legacy in-extension location when folder_paths isn't available
(e.g. under pytest, run outside a ComfyUI checkout) or hasn't finished
initializing yet.
"""

import shutil
from pathlib import Path
from typing import Callable

_DB_FILENAME = "smart_queue.sqlite3"


def resolve_db_path(
    extension_dir: Path,
    get_system_user_directory: Callable[[str], str] | None,
) -> Path:
    legacy_path = extension_dir / _DB_FILENAME

    if get_system_user_directory is None:
        return legacy_path

    try:
        user_dir = Path(get_system_user_directory("smart_queue"))
        user_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return legacy_path

    new_path = user_dir / _DB_FILENAME
    if not new_path.exists() and legacy_path.exists():
        shutil.copy2(legacy_path, new_path)

    return new_path
