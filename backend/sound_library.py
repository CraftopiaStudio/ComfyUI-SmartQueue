"""List and resolve user-supplied notification sounds in the extension's web dir.

The browser plays notification sounds over http, from ComfyUI's /extensions
mount. It cannot play a raw filesystem path: `new URL("D:/x.wav", base)`
resolves to `file:///D:/x.wav`, and a page served over http:// is not allowed
to load a file:// subresource. So a custom sound has to physically live under
web/sounds/custom/, and the browser gets a path relative to web/, which the JS
resolves against `import.meta.url` into a normal http URL.

The node offers the files in that folder as a dropdown, the same way
LoadImage lists the input folder, so the widget never holds a free-form path.
Nothing here opens or reads a sound: the files are only listed and checked for
existence, and the browser fetches them through ComfyUI's static mount.
"""

from __future__ import annotations

from pathlib import Path

# Relative to web/: what the browser hands to `new URL(..., import.meta.url)`.
WEB_SUBDIR = "sounds/custom"

# First dropdown entry, so the widget has a valid value while the folder is
# still empty.
NO_CUSTOM_SOUND = "None"

AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".webm")


def web_root() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


def custom_sounds_dir(root: Path | None = None) -> Path:
    return (root if root is not None else web_root()) / "sounds" / "custom"


def list_custom_sounds(root: Path | None = None) -> list[str]:
    """File names of the playable sounds in the custom dir, for the dropdown."""
    folder = custom_sounds_dir(root)
    if not folder.is_dir():
        return []
    names = [
        entry.name
        for entry in folder.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return sorted(names, key=str.lower)


def resolve(value: str, root: Path | None = None) -> Path | None:
    """Map a custom_sound_path widget value to a file in the custom dir, or None.

    Accepts the bare file name the dropdown stores, and the `sounds/custom/<name>`
    form that saved workflows from before the dropdown still carry. Anything
    else resolves to None, including absolute paths, which the browser could
    never play anyway.

    The value comes from a workflow, so it is untrusted text. It is never joined
    onto a path: it is matched against the directory listing and the listed
    name is returned. A joined name such as `C:x.wav` would replace the base
    directory on Windows. Matching ignores case because the filesystem does.
    """
    if not value:
        return None

    name = str(value).replace("\\", "/").strip()
    prefix = WEB_SUBDIR + "/"
    if name.startswith(prefix):
        name = name[len(prefix):]

    if not name or name == NO_CUSTOM_SOUND:
        return None

    wanted = name.casefold()
    for listed in list_custom_sounds(root):
        if listed.casefold() == wanted:
            return custom_sounds_dir(root) / listed
    return None


def to_web_relative(path: Path) -> str:
    return f"{WEB_SUBDIR}/{path.name}"
