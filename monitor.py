# monitor.py
import logging
import random
import time
log = logging.getLogger("monitor")

def _resolve_commute(danchi, api, table) -> int:
    """通过站名反查 station_cd → 通勤时间。查不到返回 60（0 分，最坏情况）。"""
    try:
        hits = api.suggest_station(danchi.station_name) if danchi.station_name else []
        for h in hits:
            cd = str(h["value"])
            if cd in table:
                return table[cd][0]
    except Exception:
        pass
    return 60

def run_monitor(cfg, api, db, notify_fn=None, comment_fn=None):
    import costtime
    import models as M
    import score as S
    if notify_fn is None:
        import notify
        notify_fn = notify.notify_new_room
    if comment_fn is None:
        import llm_comment
        comment_fn = llm_comment.llm_comment

    stat = {"danchi_checked": 0, "new_rooms": 0, "pushed": 0, "errors": []}
    table = costtime.parse_cost_time(
        api.get_cost_time_xml(cfg.destination.station_cd),
        cfg.destination.commute_max_min, cfg.destination.change_max)
    cond = costtime.build_station_condition(cfg.destination.station_cd, table,
                                            cfg.destination.commute_max_min, cfg.destination.change_max)
    webhook = getattr(getattr(cfg, "discord", None), "webhook_url", "")

    for area in cfg.areas:
        try:
            danchi_list = api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo")
        except Exception as e:
            stat["errors"].append(f"area {area}: {e}"); continue
        for d in danchi_list:
            db.upsert_danchi_from_search(d)
            if int(d.get("roomCount") or 0) <= 0:
                db.log_poll(d["id"], 0, [])
                continue
            stat["danchi_checked"] += 1
            try:
                danchi = M.parse_danchi(d, "tokyo")
                danchi.commute_min = _resolve_commute(danchi, api, table)
                # 电梯在团地级详情里
                d_detail = api.get_danchi_detail(d["id"])
                danchi.has_elevator = "エレベーター" in (d_detail.get("facility") or "")
                rooms = api.get_room_list(d["id"], cond, cfg.wide_filter, "tokyo")
            except Exception as e:
                stat["errors"].append(f"{d['id']}: {e}"); continue
            current_ids = []
            for r in rooms:
                room = M.parse_room(r, danchi)
                current_ids.append(room.room_id)
                if not db.is_new_room(room.room_id):
                    continue
                try:
                    detail = api.get_room_detail(d["id"], room.room_id)
                    M.enrich_room_from_detail(room, detail, cfg.precise.renovated_keywords)
                except Exception as e:
                    stat["errors"].append(f"detail {room.room_id}: {e}")
                ok, score, reason = S.should_push(room, cfg)
                db.mark_room_seen(room.room_id, d["id"])
                stat["new_rooms"] += 1
                if ok:
                    if notify_fn(webhook, room, score, reason):
                        stat["pushed"] += 1
                        comment = comment_fn(room) if webhook else ""
                        if comment:
                            import notify
                            notify.notify_llm_comment(webhook, room, comment)
                db.conn.execute("INSERT OR IGNORE INTO history(room_id,danchi_id,score,detail) VALUES(?,?,?,?)",
                                (room.room_id, d["id"], score, reason))
                db.conn.commit()
            db.log_poll(d["id"], len(current_ids), current_ids)
            time.sleep(random.uniform(0.2, 0.5))
    return stat
