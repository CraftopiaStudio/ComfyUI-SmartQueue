from backend.persistence import (
    init_db,
    add_queue_item,
    list_queue_items,
    reorder_queue_items,
    remove_queue_item,
    mark_completed,
    list_history,
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
