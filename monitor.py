# monitor.py
import logging
import random
import time
log = logging.getLogger("monitor")

_cost_time_cache = {}  # station_cd -> (fetched_at, table, cond)

def _load_cost_time(cfg, api):
    """cost-time XML 是静态文件（~185KB）：解析结果缓存 ≤24h，避免每轮轮询重复下载。"""
    global _cost_time_cache
    cd = cfg.destination.station_cd
    now = time.time()
    entry = _cost_time_cache.get(cd)
    if entry is not None and now - entry[0] < 24 * 3600:
        return entry[1], entry[2]
    import costtime
    xml = api.get_cost_time_xml(cd)
    table = costtime.parse_cost_time(xml, cfg.destination.commute_max_min, cfg.destination.change_max)
    cond = costtime.build_station_condition(cd, table, cfg.destination.commute_max_min, cfg.destination.change_max)
    _cost_time_cache[cd] = (now, table, cond)
    return table, cond

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
    table, cond = _load_cost_time(cfg, api)
    webhook = getattr(getattr(cfg, "discord", None), "webhook_url", "")

    for area in cfg.areas:
        try:
            danchi_list = api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo")
        except Exception as e:
            stat["errors"].append(f"area {area}: {e}")
            log.warning("获取团地列表失败 area %s: %s", area, e)
            continue
        for d in danchi_list:
            db.upsert_danchi_from_search(d)
            if int(d.get("roomCount") or 0) <= 0:
                db.log_poll(d["id"], 0, [])
                continue
            stat["danchi_checked"] += 1
            try:
                danchi = M.parse_danchi(d, "tokyo")
                static = db.get_danchi_static(d["id"])
                if static is None:
                    # 冷启动/未刷新：在线解析并写回 DB；之后每轮直接读库，不再调 suggest/detail API
                    danchi.commute_min = costtime.resolve_commute_min(danchi.station_name, api, table)
                    d_detail = api.get_danchi_detail(d["id"])
                    danchi.has_elevator = "エレベーター" in (d_detail.get("facility") or "")
                    db.set_danchi_static(d["id"], danchi.commute_min, danchi.has_elevator)
                else:
                    danchi.commute_min, danchi.has_elevator = static
                rooms = api.get_room_list(d["id"], cond, cfg.wide_filter, "tokyo")
            except Exception as e:
                stat["errors"].append(f"{d['id']}: {e}")
                log.warning("处理团地失败 %s: %s", d["id"], e)
                continue
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
                    # 详情失败：数据未验证，不推也不标记 seen → 下次轮询重试
                    stat["errors"].append(f"detail {room.room_id}: {e}")
                    log.warning("房间详情获取失败，跳过本轮 %s: %s", room.room_id, e)
                    continue
                ok, score, reason = S.should_push(room, cfg)
                stat["new_rooms"] += 1
                if not ok:
                    # 决定不推 → 标记 seen（无需重试）
                    db.mark_room_seen(room.room_id, d["id"])
                    db.conn.execute("INSERT OR IGNORE INTO history(room_id,danchi_id,score,detail) VALUES(?,?,?,?)",
                                    (room.room_id, d["id"], score, reason))
                    db.conn.commit()
                    continue
                # 推送成功才标记 seen；推送失败保持"新"，下次轮询重试
                if notify_fn(webhook, room, score, reason):
                    stat["pushed"] += 1
                    db.mark_room_seen(room.room_id, d["id"])
                    db.conn.execute("INSERT OR IGNORE INTO history(room_id,danchi_id,score,detail) VALUES(?,?,?,?)",
                                    (room.room_id, d["id"], score, reason))
                    db.conn.commit()
                    comment = comment_fn(room) if webhook else ""
                    if comment:
                        import notify
                        notify.notify_llm_comment(webhook, room, comment)
                else:
                    log.warning("Discord 推送失败，房间保持未标记（下次轮询重试） %s", room.room_id)
            db.log_poll(d["id"], len(current_ids), current_ids)
            time.sleep(random.uniform(0.2, 0.5))
    return stat
