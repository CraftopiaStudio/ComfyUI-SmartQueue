import pytest

from backend.sound_library import (
    WEB_SUBDIR,
    custom_sounds_dir,
    import_sound,
    resolve,
)


def _wav(tmp_path, name="beep.wav", data=b"RIFF----WAVEfmt "):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_import_copies_into_web_dir_and_returns_relative_path(tmp_path):
    root = tmp_path / "web"
    src = _wav(tmp_path)

    rel = import_sound(src, root=root)

    assert rel.startswith(WEB_SUBDIR + "/")
    assert rel.endswith(".wav")
    # The stored value has to be web-relative, not a filesystem path: the
    # browser resolves it against import.meta.url into an http URL.
    assert ":" not in rel and "\\" not in rel
    assert (custom_sounds_dir(root) / rel.split("/")[-1]).read_bytes() == src.read_bytes()


def test_importing_the_same_file_twice_reuses_one_copy(tmp_path):
    root = tmp_path / "web"
    src = _wav(tmp_path)

    first = import_sound(src, root=root)
    second = import_sound(src, root=root)

    assert first == second
    assert len(list(custom_sounds_dir(root).iterdir())) == 1


def test_different_files_sharing_a_name_do_not_collide(tmp_path):
    root = tmp_path / "web"
    a = _wav(tmp_path / "a", "beep.wav", b"RIFF-one")
    b = _wav(tmp_path / "b", "beep.wav", b"RIFF-two")

    rel_a = import_sound(a, root=root)
    rel_b = import_sound(b, root=root)

    assert rel_a != rel_b
    assert len(list(custom_sounds_dir(root).iterdir())) == 2


def test_import_rejects_a_non_audio_extension(tmp_path):
    root = tmp_path / "web"
    src = tmp_path / "notes.txt"
    src.write_bytes(b"nope")

    with pytest.raises(ValueError, match="Unsupported audio format"):
        import_sound(src, root=root)


def test_import_rejects_a_missing_file(tmp_path):
    with pytest.raises(ValueError, match="Not a file"):
        import_sound(tmp_path / "gone.wav", root=tmp_path / "web")


def test_resolve_finds_an_imported_sound(tmp_path):
    root = tmp_path / "web"
    rel = import_sound(_wav(tmp_path), root=root)

    assert resolve(rel, root=root) is not None


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
    rel = import_sound(_wav(tmp_path), root=root)
    (custom_sounds_dir(root) / rel.split("/")[-1]).unlink()

    assert resolve(rel, root=root) is None
