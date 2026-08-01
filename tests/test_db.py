from db import DB

def make_db():
    return DB(":memory:")

def test_upsert_danchi_returns_new_flag():
    db = make_db()
    db.init()
    d = {"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}
    assert db.upsert_danchi_from_search(d) is True
    assert db.upsert_danchi_from_search(d) is False  # 已存在

def test_room_seen_flow():
    db = make_db()
    db.init()
    assert db.is_new_room("r1") is True
    db.mark_room_seen("r1", "20_2600")
    assert db.is_new_room("r1") is False

def test_poll_log_written():
    db = make_db()
    db.init()
    db.log_poll("20_2600", 10, ["a","b"])
    rows = db.fetch_poll_log("20_2600", 5)
    assert len(rows) == 1
    assert rows[0]["vacancy_count"] == 10

def test_target_danchi_list():
    db = make_db()
    db.init()
    db.upsert_danchi_from_search({"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10})
    assert db.is_target_danchi("20_2600") is True
    assert len(db.get_all_target_danchi()) == 1
