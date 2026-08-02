# stats.py — 月度房间流统计
# 从 poll_log 的相邻快照 diff，推导每个团地房间的新增(add)/减少(remove)事件（带时间），
# 按 period(YYYY-MM) 归档保存到 room_flow 表。由 main.py 每月触发一次。
import json
import logging
import time

log = logging.getLogger("stats")


def analyze_room_flow(db, days: int = 30) -> int:
    """分析最近 days 天的 poll_log，生成新增/减少事件并入库。返回事件数。"""
    period = time.strftime("%Y-%m")
    rows = db.conn.execute(
        "SELECT danchi_id, polled_at, room_ids FROM poll_log "
        "WHERE polled_at >= datetime('now','localtime','-%d days') "
        "ORDER BY danchi_id, polled_at" % int(days)).fetchall()

    events = []  # (danchi_id, room_id, event, event_at)
    prev = {}    # danchi_id -> set(room_ids)
    for row in rows:
        did, ts, ids_json = row["danchi_id"], row["polled_at"], row["room_ids"]
        cur = set(json.loads(ids_json) if ids_json else [])
        old = prev.get(did)
        if old is not None:
            for rid in sorted(cur - old):
                events.append((did, rid, "add", ts))
            for rid in sorted(old - cur):
                events.append((did, rid, "remove", ts))
        prev[did] = cur

    # 幂等：先清掉本月的旧记录再写入（跨月历史保留）
    db.conn.execute("DELETE FROM room_flow WHERE period=?", (period,))
    db.conn.executemany(
        "INSERT INTO room_flow(danchi_id, room_id, event, event_at, period) VALUES(?,?,?,?,?)",
        [(d, r, e, t, period) for d, r, e, t in events])
    db.conn.commit()
    log.info("room_flow[%s]: 记录 %d 条新增/减少事件", period, len(events))
    return len(events)
