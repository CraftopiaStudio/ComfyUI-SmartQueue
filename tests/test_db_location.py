from pathlib import Path

from backend.db_location import resolve_db_path


def test_falls_back_to_extension_dir_when_no_getter(tmp_path):
    result = resolve_db_path(tmp_path, get_system_user_directory=None)
    assert result == tmp_path / "smart_queue.sqlite3"


def test_falls_back_to_extension_dir_when_getter_raises(tmp_path):
    def _raises(name):
        raise RuntimeError("folder_paths not fully initialized")

    result = resolve_db_path(tmp_path, get_system_user_directory=_raises)
    assert result == tmp_path / "smart_queue.sqlite3"


def test_uses_system_user_directory_when_available(tmp_path):
    user_dir = tmp_path / "user" / "__system_smart_queue"

    result = resolve_db_path(tmp_path, get_system_user_directory=lambda name: str(user_dir))

    assert result == user_dir / "smart_queue.sqlite3"
    assert user_dir.is_dir()


def test_migrates_existing_legacy_db_on_first_run(tmp_path):
    legacy_db = tmp_path / "smart_queue.sqlite3"
    legacy_db.write_bytes(b"fake-sqlite-bytes")
    user_dir = tmp_path / "user" / "__system_smart_queue"

    result = resolve_db_path(tmp_path, get_system_user_directory=lambda name: str(user_dir))

    assert result.read_bytes() == b"fake-sqlite-bytes"
    assert legacy_db.exists()  # copied, not moved — old file stays as a safety net


def test_does_not_overwrite_existing_new_location_db(tmp_path):
    legacy_db = tmp_path / "smart_queue.sqlite3"
    legacy_db.write_bytes(b"old-bytes")
    user_dir = tmp_path / "user" / "__system_smart_queue"
    user_dir.mkdir(parents=True)
    new_db = user_dir / "smart_queue.sqlite3"
    new_db.write_bytes(b"already-migrated-bytes")

    result = resolve_db_path(tmp_path, get_system_user_directory=lambda name: str(user_dir))

    assert result.read_bytes() == b"already-migrated-bytes"
