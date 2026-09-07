"""Resolve user-supplied notification sounds living in the extension's web dir.

The browser plays notification sounds over http, from ComfyUI's /extensions
mount. It cannot play a raw filesystem path: `new URL("D:/x.wav", base)`
resolves to `file:///D:/x.wav`, and a page served over http:// is not allowed
to load a file:// subresource — confirmed live, the same existing .wav loads
fine over http and fails with media error code 4 over file://. So a custom
sound has to physically live under web/, and `custom_sound_path` stores a path
relative to web/, which the JS resolves against `import.meta.url` into a normal
http URL.

Getting a file in there is a manual step: drop it into web/sounds/custom/ and
type `sounds/custom/<name>` into the node's `custom_sound_path` widget. There
used to be a Browse button backed by a native file dialog, but that meant an
unauthenticated HTTP route spawning a PowerShell/osascript/zenity process,
which is the exact shape the Comfy Registry bans under policy-v0.2 (see the
design doc, § "Dropping the native sound picker"). Resolving instead of
serving arbitrary paths also keeps the extension from handing out any file on
disk on request.
"""

from __future__ import annotations

from pathlib import Path

# Relative to web/ — this is what gets stored in the node's custom_sound_path
# widget and handed to `new URL(..., import.meta.url)` in the browser.
WEB_SUBDIR = "sounds/custom"


def web_root() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


def custom_sounds_dir(root: Path | None = None) -> Path:
    return (root if root is not None else web_root()) / "sounds" / "custom"


def resolve(web_relative: str, root: Path | None = None) -> Path | None:
    """Map a stored custom_sound_path back to a real file, or None.

    Returns None for anything that isn't a file living in our own custom
    sounds dir — including the absolute Windows paths stored by versions
    before this module existed, which the browser could never play anyway.
    """
    if not web_relative:
        return None

    rel = str(web_relative).replace("\\", "/").strip()
    prefix = WEB_SUBDIR + "/"
    if not rel.startswith(prefix):
        return None

    name = rel[len(prefix):]
    # No traversal, no nesting: this dir is flat by construction.
    if not name or "/" in name or name in (".", ".."):
        return None

    candidate = custom_sounds_dir(root) / name
    return candidate if candidate.is_file() else None
