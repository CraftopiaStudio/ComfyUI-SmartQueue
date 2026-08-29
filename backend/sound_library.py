"""Import user-picked notification sounds into the extension's own web dir.

The browser plays notification sounds over http, from ComfyUI's /extensions
mount. It cannot play a raw filesystem path: `new URL("D:/x.wav", base)`
resolves to `file:///D:/x.wav`, and a page served over http:// is not allowed
to load a file:// subresource — confirmed live, the same existing .wav loads
fine over http and fails with media error code 4 over file://. So a picked
sound is copied in here and stored as a path relative to web/, which the JS
resolves against `import.meta.url` into a normal http URL.

Copying (rather than serving arbitrary paths through an endpoint) also keeps
the extension from handing out any file on disk on request.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

# Relative to web/ — this is what gets stored in the node's custom_sound_path
# widget and handed to `new URL(..., import.meta.url)` in the browser.
WEB_SUBDIR = "sounds/custom"

ALLOWED_SUFFIXES = {".wav", ".mp3", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus", ".webm"}

_UNSAFE_STEM_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def web_root() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


def custom_sounds_dir(root: Path | None = None) -> Path:
    return (root if root is not None else web_root()) / "sounds" / "custom"


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def import_sound(src: str | Path, root: Path | None = None) -> str:
    """Copy `src` into web/sounds/custom; return its web-relative path.

    Raises ValueError for a missing file or an unsupported audio format.
    """
    source = Path(src)
    if not source.is_file():
        raise ValueError(f"Not a file: {src}")

    suffix = source.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"Unsupported audio format '{suffix or '(none)'}' — "
            f"expected one of: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )

    dest_dir = custom_sounds_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Content-addressed filename. Picking the same file twice reuses the one
    # copy instead of piling up duplicates, and two different files that happen
    # to share a basename can never overwrite each other's sound.
    stem = _UNSAFE_STEM_CHARS.sub("_", source.stem)[:60] or "sound"
    dest = dest_dir / f"{stem}-{_content_hash(source)}{suffix}"
    if not dest.exists():
        shutil.copyfile(source, dest)
    return f"{WEB_SUBDIR}/{dest.name}"


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
