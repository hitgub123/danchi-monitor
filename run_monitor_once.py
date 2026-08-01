# run_monitor_once.py — 手动单次运行（调试用）
import logging
logging.basicConfig(level=logging.INFO)
from config import load_config
from ur_api import UrApi
from db import DB
from monitor import run_monitor

cfg = load_config("config.yaml")
db = DB("data.db"); db.init()
api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
stat = run_monitor(cfg, api, db)
print(stat)
