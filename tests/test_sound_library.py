import pytest

from backend.sound_library import (
    WEB_SUBDIR,
    custom_sounds_dir,
    resolve,
)


def _custom_sound(root, name="beep.wav", data=b"RIFF----WAVEfmt "):
    """Place a file the way a user now does it: by hand, into web/sounds/custom."""
    dest_dir = custom_sounds_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    path.write_bytes(data)
    return path


def test_resolve_finds_a_sound_dropped_into_the_custom_dir(tmp_path):
    root = tmp_path / "web"
    _custom_sound(root)

    assert resolve(f"{WEB_SUBDIR}/beep.wav", root=root) is not None


def test_resolve_rejects_paths_from_before_this_module_existed(tmp_path):
    # Older builds stored the raw picked path, which the browser could never
    # load — those must resolve to None so the node falls back to the default
    # tone and says so, rather than silently playing nothing meaningful.
    assert resolve("D:/sounds/mine.wav", root=tmp_path / "web") is None
    assert resolve("D:\\sounds\\mine.wav", root=tmp_path / "web") is None


@pytest.mark.parametrize(
    "value",
    ["", f"{WEB_SUBDIR}/", f"{WEB_SUBDIR}/../../secret.wav", f"{WEB_SUBDIR}/nested/x.wav", "sounds/default.wav"],
)
def test_resolve_rejects_junk_and_traversal(tmp_path, value):
    assert resolve(value, root=tmp_path / "web") is None


def test_resolve_returns_none_when_the_file_was_deleted(tmp_path):
    root = tmp_path / "web"
    _custom_sound(root).unlink()

    assert resolve(f"{WEB_SUBDIR}/beep.wav", root=root) is None
