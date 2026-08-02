import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_danchi (
    danchi_id TEXT PRIMARY KEY, name TEXT, skcs TEXT,
    url TEXT,
    prefecture TEXT, station_name TEXT, walk_min INTEGER,
    commute_min INTEGER, has_elevator INTEGER,
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
    detail TEXT, url TEXT, llm_comment TEXT, found_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""

class DB:
    def __init__(self, db_path="data.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init(self):
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """老库补列：target_danchi 加 commute_min/has_elevator/url，history 加 url。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(target_danchi)")}
        if "commute_min" not in cols:
            self.conn.execute("ALTER TABLE target_danchi ADD COLUMN commute_min INTEGER")
        if "has_elevator" not in cols:
            self.conn.execute("ALTER TABLE target_danchi ADD COLUMN has_elevator INTEGER")
        if "url" not in cols:
            self.conn.execute("ALTER TABLE target_danchi ADD COLUMN url TEXT")
        hcols = {r[1] for r in self.conn.execute("PRAGMA table_info(history)")}
        if "url" not in hcols:
            self.conn.execute("ALTER TABLE history ADD COLUMN url TEXT")

    def upsert_danchi_from_search(self, d: dict) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (d["id"],))
        exists = cur.fetchone() is not None
        url = d.get("bukkenUrl") or ""
        if url and not url.startswith("http"):
            url = "https://www.ur-net.go.jp" + url
        self.conn.execute(
            "INSERT INTO target_danchi(danchi_id,name,skcs,url) VALUES(?,?,?,?) "
            "ON CONFLICT(danchi_id) DO UPDATE SET name=excluded.name, skcs=excluded.skcs, url=excluded.url",
            (d["id"], d.get("name"), d.get("skcs") or "", url))
        self.conn.commit()
        return not exists

    def is_target_danchi(self, danchi_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM target_danchi WHERE danchi_id=?", (danchi_id,))
        return cur.fetchone() is not None

    def get_all_target_danchi(self) -> list:
        rows = self.conn.execute(
            "SELECT danchi_id,name,skcs,url,prefecture FROM target_danchi WHERE active=1")
        return [dict(r) for r in rows]

    def count_danchi(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM target_danchi").fetchone()
        return row[0] if row else 0

    def get_meta(self, key: str):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))
        self.conn.commit()

    def get_danchi_static(self, danchi_id: str):
        """团地级静态信息（通勤分钟/电梯）。未刷新（commute_min 为 NULL）或不存在 → None。"""
        row = self.conn.execute(
            "SELECT commute_min, has_elevator FROM target_danchi WHERE danchi_id=?",
            (danchi_id,)).fetchone()
        if row is None or row["commute_min"] is None:
            return None
        return row["commute_min"], bool(row["has_elevator"])

    def set_danchi_static(self, danchi_id: str, commute_min: int, has_elevator: bool) -> None:
        self.conn.execute(
            "UPDATE target_danchi SET commute_min=?, has_elevator=? WHERE danchi_id=?",
            (int(commute_min), 1 if has_elevator else 0, danchi_id))
        self.conn.commit()

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
