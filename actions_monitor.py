# actions_monitor.py — GitHub Actions 实时上新监控
# 每次运行: 抓全量在架房 → 快照 diff → 上新打分推送 Discord → 覆盖快照(有变化才 commit)
import json
import os
import subprocess
import sys

import costtime
import models as M
import notify
import score as S
from config import load_config
from ur_api import UrApi

SNAPSHOT_PATH = "snapshot/rooms.json"


# ---- 快照读写 ----

def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("table", {})
        data.setdefault("danchi_static", {})
        data.setdefault("rooms", {})
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_snapshot(path, snapshot):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


# ---- diff / 基线 ----

def diff_new(current, previous):
    """上一快照中没有的新房间 {room_id: info}；previous 空(首跑) → {} 静默基线。"""
    if not previous:
        return {}
    return {rid: info for rid, info in current.items() if rid not in previous}


# ---- 通勤表: cond 由 table 派生; table 静态缓存, 避免每次重下 185KB XML ----

def build_cond(dest_cd, table, cost_max, change_max):
    return costtime.build_station_condition(dest_cd, table, cost_max, change_max)


def _load_table(api, cfg, snapshot):
    if snapshot.get("table"):
        return snapshot["table"]
    xml = api.get_cost_time_xml(cfg.destination.station_cd)
    table = costtime.parse_cost_time(xml, cfg.destination.commute_max_min, cfg.destination.change_max)
    snapshot["table"] = table
    return table
