from backend.persistence import (
    init_db,
    add_queue_item,
    list_queue_items,
    reorder_queue_items,
    remove_queue_item,
    mark_completed,
    list_history,
    save_held_items,
    load_held_items,
    set_queue_item_status,
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
