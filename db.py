import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_danchi (
    danchi_id TEXT PRIMARY KEY, name TEXT, skcs TEXT,
    prefecture TEXT, station_name TEXT, walk_min INTEGER,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS seen_rooms (
    room_id TEXT PRIMARY KEY, danchi_id TEXT,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    last_seen TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polled_at TEXT DEFAULT (datetime('now','localtime')),
    danchi_id TEXT, vacancy_count INTEGER, room_ids TEXT
);
CREATE TABLE IF NOT EXISTS history (
    room_id TEXT PRIMARY KEY, danchi_id TEXT, score REAL,
    detail TEXT, llm_comment TEXT, found_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

class DB:
    def __init__(self, db_path="data.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_danchi_from_search(self, d: dict) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (d["id"],))
        exists = cur.fetchone() is not None
        self.conn.execute(
            "INSERT INTO target_danchi(danchi_id,name,skcs) VALUES(?,?,?) "
            "ON CONFLICT(danchi_id) DO UPDATE SET name=excluded.name, skcs=excluded.skcs",
            (d["id"], d.get("name"), d.get("skcs") or ""))
        self.conn.commit()
        return not exists

    def is_target_danchi(self, danchi_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (danchi_id,))
        return cur.fetchone() is not None

    def get_all_target_danchi(self) -> list:
        rows = self.conn.execute(
            "SELECT danchi_id,name,skcs,prefecture FROM target_danchi WHERE active=1")
        return [dict(r) for r in rows]

    def is_new_room(self, room_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_rooms WHERE room_id=?", (room_id,))
        return cur.fetchone() is None

    def mark_room_seen(self, room_id: str, danchi_id: str) -> None:
        self.conn.execute(
            "INSERT INTO seen_rooms(room_id,danchi_id) VALUES(?,?) "
            "ON CONFLICT(room_id) DO UPDATE SET last_seen=datetime('now','localtime')",
            (room_id, danchi_id))
        self.conn.commit()

    def log_poll(self, danchi_id: str, vacancy_count: int, room_ids: list) -> None:
        self.conn.execute(
            "INSERT INTO poll_log(danchi_id,vacancy_count,room_ids) VALUES(?,?,?)",
            (danchi_id, vacancy_count, json.dumps(room_ids)))
        self.conn.commit()

    def fetch_poll_log(self, danchi_id: str, limit: int = 50) -> list:
        rows = self.conn.execute(
            "SELECT * FROM poll_log WHERE danchi_id=? ORDER BY id DESC LIMIT ?",
            (danchi_id, limit))
        return [dict(r) for r in rows]
