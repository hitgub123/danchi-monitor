# main.py
import logging
import time
import random
from config import load_config
from ur_api import UrApi
from db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("main")

def pick_interval(cfg, hour: int) -> int:
    if 8 <= hour < 22:
        return cfg.day_interval_min
    return cfg.night_interval_min

def _api(cfg):
    return UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)

def _loop(cfg, db, api):
    from monitor import run_monitor
    from discover import run_discover
    last_discover = 0.0
    while True:
        now = time.time()
        if now - last_discover > 24 * 3600:  # 至少每24h跑一次 discover
            try:
                run_discover(cfg, api, db)
                last_discover = now
            except Exception:
                log.exception("discover 失败")
        try:
            run_monitor(cfg, api, db)
        except Exception:
            log.exception("monitor 失败")
        interval = pick_interval(cfg, time.localtime().tm_hour) * 60
        time.sleep(interval + random.uniform(0, 5))

def main():
    cfg = load_config("config.yaml")
    db = DB("data.db"); db.init()
    api = _api(cfg)
    _loop(cfg, db, api)

if __name__ == "__main__":
    main()
