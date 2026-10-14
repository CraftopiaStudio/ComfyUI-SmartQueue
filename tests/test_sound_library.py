import pytest

from backend.sound_library import (
    NO_CUSTOM_SOUND,
    WEB_SUBDIR,
    custom_sounds_dir,
    list_custom_sounds,
    resolve,
    to_web_relative,
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


def test_list_is_empty_when_the_custom_dir_does_not_exist(tmp_path):
    assert list_custom_sounds(root=tmp_path / "web") == []


def test_list_shows_audio_files_sorted_and_skips_everything_else(tmp_path):
    root = tmp_path / "web"
    _custom_sound(root, "zap.mp3")
    _custom_sound(root, "Beep.wav")
    _custom_sound(root, "notes.txt")
    _custom_sound(root, ".hidden.wav")
    (custom_sounds_dir(root) / "subdir.wav").mkdir()

    assert list_custom_sounds(root=root) == ["Beep.wav", "zap.mp3"]


def test_resolve_accepts_the_bare_name_the_dropdown_stores(tmp_path):
    root = tmp_path / "web"
    _custom_sound(root)

    assert resolve("beep.wav", root=root) == custom_sounds_dir(root) / "beep.wav"


def test_resolve_treats_the_none_option_as_no_sound(tmp_path):
    root = tmp_path / "web"
    _custom_sound(root, NO_CUSTOM_SOUND)

    assert resolve(NO_CUSTOM_SOUND, root=root) is None


@pytest.mark.parametrize("value", ["../secret.wav", "..", ".", "a/b.wav", "a\b.wav"])
def test_resolve_rejects_traversal_in_bare_names(tmp_path, value):
    assert resolve(value, root=tmp_path / "web") is None


def test_web_relative_path_is_what_the_browser_resolves_against_web(tmp_path):
    root = tmp_path / "web"
    path = _custom_sound(root)

    assert to_web_relative(path) == f"{WEB_SUBDIR}/beep.wav"
