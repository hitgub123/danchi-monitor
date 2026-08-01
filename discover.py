# discover.py
import logging

import costtime
log = logging.getLogger("discover")

def _refresh_danchi_static(api, db, d, table):
    """团地级静态信息（通勤分钟/电梯）只在 discover 时刷新入库；monitor 每轮直接读库。"""
    try:
        import models as M
        danchi = M.parse_danchi(d, "tokyo")
        commute = costtime.resolve_commute_min(danchi.station_name, api, table)
        elev = "エレベーター" in (api.get_danchi_detail(d["id"]).get("facility") or "")
        db.set_danchi_static(d["id"], commute, elev)
    except Exception:
        log.exception("刷新团地静态信息失败，跳过 %s", d.get("id"))

def run_discover(cfg, api, db) -> int:
    table = {}
    try:
        xml = api.get_cost_time_xml(cfg.destination.station_cd)
        table = costtime.parse_cost_time(xml, cfg.destination.commute_max_min, cfg.destination.change_max)
    except Exception:
        log.exception("下载通勤表失败")
        raise
    cond = costtime.build_station_condition(cfg.destination.station_cd, table,
                                            cfg.destination.commute_max_min, cfg.destination.change_max)
    added = 0
    for area in cfg.areas:
        try:
            danchi_list = api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo")
            for d in danchi_list:
                if db.upsert_danchi_from_search(d):
                    added += 1
                _refresh_danchi_static(api, db, d, table)
        except Exception:
            log.exception("area %s 处理失败，跳过", area)
    log.info("discover 完成，新增 %d 个目标团地", added)
    return added
