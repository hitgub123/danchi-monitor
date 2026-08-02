# main.py
import logging
import time
import random
from config import load_config
from ur_api import UrApi
from db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("main")

DISCOVER_INTERVAL_SEC = 30 * 24 * 3600  # 月度 discover：距上次成功满30天再跑（开机不主动跑）

def pick_interval(cfg, hour: int) -> int:
    if 8 <= hour < 22:
        return cfg.day_interval_min
    return cfg.night_interval_min

def _api(cfg):
    return UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)

def _loop(cfg, db, api):
    from monitor import run_monitor
    from discover import run_discover

    # 全新库(无目标团地)才在启动时建一次清单；老库跳过，交给月度 discover
    try:
        if db.count_danchi() == 0:
            run_discover(cfg, api, db)
            db.set_meta("last_discover", str(time.time()))
    except Exception:
        log.exception("首次 discover 失败")

    last_prune = time.time()  # 启动不立即清理，满24h后每天一次

    while True:
        # poll_log 保留策略：超 keep_days 的旧快照每天清理一次
        if time.time() - last_prune > 24 * 3600:
            try:
                pruned = db.prune_poll_log(cfg.schedule.poll_log_keep_days)
                log.info("清理 poll_log 超 %s 天旧数据 %s 行", cfg.schedule.poll_log_keep_days, pruned)
            except Exception:
                log.exception("poll_log 清理失败")
            last_prune = time.time()
        # 月度 discover：距上次成功满30天 或 从未成功过 → 跑一次（失败则下轮自动重试）
        try:
            last = db.get_meta("last_discover")
            if last is None or time.time() - float(last) > DISCOVER_INTERVAL_SEC:
                run_discover(cfg, api, db)
                db.set_meta("last_discover", str(time.time()))
        except Exception:
            log.exception("discover 失败")
        # 月度统计：分析 poll_log 推房间新增/减少事件，存 room_flow
        try:
            last_s = db.get_meta("last_stats")
            if last_s is None or time.time() - float(last_s) > DISCOVER_INTERVAL_SEC:
                from stats import analyze_room_flow
                n = analyze_room_flow(db, days=30)
                db.set_meta("last_stats", str(time.time()))
                log.info("月度房间流统计: 新增/减少事件 %s 条", n)
        except Exception:
            log.exception("月度统计失败")
        try:
            stat = run_monitor(cfg, api, db)
        except Exception:
            log.exception("monitor 失败")
            stat = None
        if stat is not None:
            n_checked = stat.get("danchi_checked", 0)
            n_new = stat.get("new_rooms", 0)
            n_pushed = stat.get("pushed", 0)
            n_err = len(stat.get("errors", []))
            if n_err:
                log.warning("monitor 周期完成：checked=%s new=%s pushed=%s errors=%s",
                            n_checked, n_new, n_pushed, n_err)
            else:
                log.info("monitor 周期完成：checked=%s new=%s pushed=%s", n_checked, n_new, n_pushed)
        interval = pick_interval(cfg.schedule, time.localtime().tm_hour) * 60
        time.sleep(interval + random.uniform(0, 5))

def main():
    cfg = load_config("config.yaml")
    db = DB("data.db"); db.init()
    api = _api(cfg)
    _loop(cfg, db, api)

if __name__ == "__main__":
    main()
