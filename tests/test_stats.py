# tests/test_stats.py
import json
from db import DB
from stats import analyze_room_flow

def make_db():
    db = DB(":memory:"); db.init()
    return db

def test_analyze_room_flow_detects_add_and_remove():
    db = make_db()
    # 相邻快照 diff: t1{a,b} → t2{b,c}(a移除,c新增) → t3{b,c,d}(d新增)
    for ts, ids in [
        ("2026-08-01 08:00:00", ["a", "b"]),
        ("2026-08-01 08:05:00", ["b", "c"]),
        ("2026-08-01 08:10:00", ["b", "c", "d"]),
    ]:
        db.conn.execute("INSERT INTO poll_log(danchi_id,polled_at,room_ids) VALUES('20_2600',?,?)",
                        (ts, json.dumps(ids)))
    db.conn.commit()
    n = analyze_room_flow(db, days=30)
    assert n == 3
    ev = db.conn.execute(
        "SELECT room_id,event,event_at FROM room_flow ORDER BY event_at, room_id").fetchall()
    got = [(r["room_id"], r["event"], r["event_at"]) for r in ev]
    assert ("a", "remove", "2026-08-01 08:05:00") in got
    assert ("c", "add", "2026-08-01 08:05:00") in got
    assert ("d", "add", "2026-08-01 08:10:00") in got

def test_analyze_room_flow_idempotent_per_period():
    db = make_db()
    db.conn.execute("INSERT INTO poll_log(danchi_id,polled_at,room_ids) VALUES('20_2600','2026-08-01 08:00:00','[\"a\"]')")
    db.conn.execute("INSERT INTO poll_log(danchi_id,polled_at,room_ids) VALUES('20_2600','2026-08-01 08:05:00','[\"a\",\"b\"]')")
    db.conn.commit()
    analyze_room_flow(db, days=30)
    analyze_room_flow(db, days=30)  # 同月重跑 → 不重复
    n = db.conn.execute("SELECT COUNT(*) FROM room_flow").fetchone()[0]
    assert n == 1  # 只有 add b 一条, 重跑不翻倍
