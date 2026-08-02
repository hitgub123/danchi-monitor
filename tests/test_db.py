from db import DB

def make_db():
    return DB(":memory:")

def test_upsert_danchi_returns_new_flag():
    db = make_db()
    db.init()
    d = {"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}
    assert db.upsert_danchi_from_search(d) is True
    assert db.upsert_danchi_from_search(d) is False  # 已存在

def test_upsert_danchi_stores_url():
    db = make_db()
    db.init()
    # bukkenUrl 是相对路径 → 应存成完整 URL
    d = {"id":"20_3820","name":"神田小川町ハイツ","skcs":"千代田区",
         "bukkenUrl":"/chintai/kanto/tokyo/20_3820.html"}
    db.upsert_danchi_from_search(d)
    rows = db.get_all_target_danchi()
    assert rows[0]["url"] == "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_3820.html"
    # 无 bukkenUrl 的老响应也能兼容（存空串，不崩）
    db.upsert_danchi_from_search({"id":"20_9999","name":"x","skcs":""})
    assert db.get_all_target_danchi()[-1]["url"] == ""

def test_migrate_adds_history_url():
    # 老库 history 无 url 列，init 必须补列
    import os, sqlite3, tempfile
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE history (room_id TEXT PRIMARY KEY, danchi_id TEXT, score REAL, "
                    "detail TEXT, llm_comment TEXT, found_at TEXT DEFAULT (datetime('now','localtime')))")
        old.execute("INSERT INTO history(room_id,danchi_id,score) VALUES('r1','20_2600',80)")
        old.commit(); old.close()
        db = DB(path); db.init()
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(history)")}
        assert "url" in cols
        row = db.conn.execute("SELECT url FROM history WHERE room_id='r1'").fetchone()
        assert row[0] is None  # 旧行 url 为 NULL
    finally:
        os.remove(path)

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

def test_upsert_history_updates_latest():
    db = make_db()
    db.init()
    db.upsert_history("r1", "20_2600", 42.5, "旧理由", "https://x/1")
    first_found = db.conn.execute("SELECT found_at FROM history WHERE room_id='r1'").fetchone()[0]
    # 同一房间再次评估 → 分数/理由/链接更新, found_at 保留首次
    db.upsert_history("r1", "20_2600", 53.8, "新理由", "https://x/2")
    row = db.conn.execute("SELECT score,detail,url,found_at FROM history WHERE room_id='r1'").fetchone()
    assert (row["score"], row["detail"], row["url"]) == (53.8, "新理由", "https://x/2")
    assert row["found_at"] == first_found  # 不重复, 只更新

def test_prune_poll_log_keeps_recent():
    db = make_db()
    db.init()
    db.conn.execute("INSERT INTO poll_log(polled_at,danchi_id,vacancy_count,room_ids) "
                    "VALUES('2026-01-01 00:00:00','20_2600',3,'[]')")  # 90天前 → 删
    db.conn.execute("INSERT INTO poll_log(danchi_id,vacancy_count,room_ids) "
                    "VALUES('20_2600',3,'[]')")  # now → 留
    db.conn.commit()
    assert db.prune_poll_log(keep_days=90) == 1
    rows = db.conn.execute("SELECT * FROM poll_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["polled_at"] != "2026-01-01 00:00:00"

def test_target_danchi_list():
    db = make_db()
    db.init()
    db.upsert_danchi_from_search({"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10})
    assert db.is_target_danchi("20_2600") is True
    assert len(db.get_all_target_danchi()) == 1

def test_migrate_adds_static_columns():
    # F3：老库无 commute_min/has_elevator 列，init 必须补列；旧行视为未刷新（None）
    import os, sqlite3, tempfile
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE target_danchi (danchi_id TEXT PRIMARY KEY, name TEXT, skcs TEXT, "
                    "prefecture TEXT, station_name TEXT, walk_min INTEGER, "
                    "first_seen TEXT DEFAULT (datetime('now','localtime')), active INTEGER DEFAULT 1)")
        old.execute("INSERT INTO target_danchi(danchi_id,name) VALUES('20_2600','館ヶ丘')")
        old.commit(); old.close()
        db = DB(path); db.init()
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(target_danchi)")}
        assert "commute_min" in cols and "has_elevator" in cols
        assert db.get_danchi_static("20_2600") is None  # 旧行未刷新 → 由 monitor 冷启动回填
        db.set_danchi_static("20_2600", 40, True)
        assert db.get_danchi_static("20_2600") == (40, True)
    finally:
        os.remove(path)
