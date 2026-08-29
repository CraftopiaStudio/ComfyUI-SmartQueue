from backend.persistence import (
    init_db,
    add_queue_item,
    list_queue_items,
    reorder_queue_items,
    remove_queue_item,
    rename_queue_item,
    mark_completed,
    list_history,
    save_held_items,
    load_held_items,
    set_queue_item_status,
    delete_history_older_than,
)


def test_init_db_creates_empty_tables(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    assert list_queue_items(conn) == []
    assert list_history(conn) == []


def test_add_queue_item_appears_in_order(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="Cyberpunk Cat")
    add_queue_item(conn, prompt_id="b", name="Sunset Beach")
    items = list_queue_items(conn)
    assert [item["prompt_id"] for item in items] == ["a", "b"]
    assert items[0]["name"] == "Cyberpunk Cat"
    assert items[0]["status"] == "pending"


def test_reorder_queue_items_changes_returned_order(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    add_queue_item(conn, prompt_id="b", name="Second")
    reorder_queue_items(conn, ordered_prompt_ids=["b", "a"])
    items = list_queue_items(conn)
    assert [item["prompt_id"] for item in items] == ["b", "a"]


def test_remove_queue_item_deletes_it(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    remove_queue_item(conn, prompt_id="a")
    assert list_queue_items(conn) == []


def test_mark_completed_moves_item_into_history(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    mark_completed(conn, prompt_id="a", thumbnail_path="/thumbs/a.png")
    assert list_queue_items(conn) == []
    history = list_history(conn)
    assert len(history) == 1
    assert history[0]["prompt_id"] == "a"
    assert history[0]["thumbnail_path"] == "/thumbs/a.png"


def test_persistence_survives_reopening_the_same_file(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    conn1 = init_db(db_path)
    add_queue_item(conn1, prompt_id="a", name="First")
    conn1.close()

    conn2 = init_db(db_path)
    items = list_queue_items(conn2)
    assert len(items) == 1
    assert items[0]["prompt_id"] == "a"


def test_set_queue_item_status_updates_existing_item(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")

    set_queue_item_status(conn, prompt_id="a", status="held")

    items = list_queue_items(conn)
    assert items[0]["status"] == "held"


def test_set_queue_item_status_is_noop_for_unknown_prompt_id(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    set_queue_item_status(conn, prompt_id="ghost", status="held")
    assert list_queue_items(conn) == []


def test_load_held_items_empty_initially(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    assert load_held_items(conn) == []


def test_save_and_load_held_items_round_trips_in_order(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    items = [
        (1.0, "a", {"1": {"class_type": "X"}}, {"client_id": "c1"}, ["1"], {}),
        (2.0, "b", {"1": {"class_type": "Y"}}, {}, ["1"], {}),
    ]
    save_held_items(conn, items)

    loaded = load_held_items(conn)

    assert loaded == items


def test_save_held_items_replaces_previous_snapshot(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    save_held_items(conn, [(1.0, "a", {}, {}, [], {})])
    save_held_items(conn, [(2.0, "b", {}, {}, [], {})])

    loaded = load_held_items(conn)

    assert [item[1] for item in loaded] == ["b"]


def test_save_held_items_with_empty_list_clears_table(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    save_held_items(conn, [(1.0, "a", {}, {}, [], {})])
    save_held_items(conn, [])

    assert load_held_items(conn) == []


def test_held_items_survive_reopening_the_same_file(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    conn1 = init_db(db_path)
    save_held_items(conn1, [(1.0, "a", {"k": "v"}, {}, [], {})])
    conn1.close()

    conn2 = init_db(db_path)
    loaded = load_held_items(conn2)
    assert len(loaded) == 1
    assert loaded[0][1] == "a"
    assert loaded[0][2] == {"k": "v"}


def test_rename_queue_item_updates_name(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="Old Name")
    rename_queue_item(conn, prompt_id="a", name="New Name")
    items = list_queue_items(conn)
    assert items[0]["name"] == "New Name"


def test_rename_queue_item_nonexistent_prompt_id_is_a_noop(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    rename_queue_item(conn, prompt_id="ghost", name="New Name")
    assert list_queue_items(conn) == []


def test_list_queue_items_filters_by_status(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    add_queue_item(conn, prompt_id="b", name="Second")
    set_queue_item_status(conn, prompt_id="b", status="held")
    held_only = list_queue_items(conn, status="held")
    assert [item["prompt_id"] for item in held_only] == ["b"]


def test_list_queue_items_filters_by_name_case_insensitive(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="Cyberpunk Cat")
    add_queue_item(conn, prompt_id="b", name="Sunset Beach")
    matches = list_queue_items(conn, name_contains="cyber")
    assert [item["prompt_id"] for item in matches] == ["a"]


def test_list_history_filters_by_name_and_date_range(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="Cyberpunk Cat")
    mark_completed(conn, prompt_id="a")
    conn.execute("UPDATE history SET completed_at = '2026-01-15T10:00:00+00:00' WHERE prompt_id = 'a'")
    conn.commit()

    assert len(list_history(conn, name_contains="cyber")) == 1
    assert len(list_history(conn, name_contains="beach")) == 0
    assert len(list_history(conn, date_from="2026-01-01", date_to="2026-01-31")) == 1
    assert len(list_history(conn, date_from="2026-02-01")) == 0


def test_delete_history_older_than_removes_only_old_rows(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="old", name="Old Job")
    mark_completed(conn, prompt_id="old")
    conn.execute("UPDATE history SET completed_at = '2020-01-01T00:00:00+00:00' WHERE prompt_id = 'old'")
    add_queue_item(conn, prompt_id="new", name="New Job")
    mark_completed(conn, prompt_id="new")
    conn.execute("UPDATE history SET completed_at = '2099-01-01T00:00:00+00:00' WHERE prompt_id = 'new'")
    conn.commit()

    deleted = delete_history_older_than(conn, cutoff_iso="2026-01-01T00:00:00+00:00")

    assert deleted == 1
    remaining = [item["prompt_id"] for item in list_history(conn)]
    assert remaining == ["new"]
