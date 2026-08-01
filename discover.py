# discover.py
import logging
log = logging.getLogger("discover")

def run_discover(cfg, api, db) -> int:
    table = {}
    try:
        xml = api.get_cost_time_xml(cfg.destination.station_cd)
        import costtime
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
        except Exception:
            log.exception("area %s 处理失败，跳过", area)
    log.info("discover 完成，新增 %d 个目标团地", added)
    return added
