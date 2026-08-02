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

    while True:
        # 月度 discover：距上次成功满30天 或 从未成功过 → 跑一次（失败则下轮自动重试）
        try:
            last = db.get_meta("last_discover")
            if last is None or time.time() - float(last) > DISCOVER_INTERVAL_SEC:
                run_discover(cfg, api, db)
                db.set_meta("last_discover", str(time.time()))
        except Exception:
            log.exception("discover 失败")
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
