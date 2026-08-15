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


# ---- 采集当前全量在架房 ----

def current_rooms(api, cfg, cond, table, danchi_static):
    """遍历 areas 收集当前全量在架房; 首次见到的团地算通勤/电梯并入缓存。返回 {room_id: info}。"""
    rooms = {}
    for area in cfg.areas:
        for d in api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo"):
            if int(d.get("roomCount") or 0) <= 0:
                continue
            did = d["id"]
            danchi = M.parse_danchi(d, "tokyo")
            if did not in danchi_static:
                commute = costtime.resolve_commute_min(danchi.station_name, api, table)
                elevator = "エレベーター" in (api.get_danchi_detail(did).get("facility") or "")
                danchi_static[did] = {"commute_min": commute, "has_elevator": elevator,
                                      "walk_min": danchi.walk_min, "name": d.get("name") or "",
                                      "skcs": d.get("skcs") or ""}
            st = danchi_static[did]
            danchi.commute_min = st["commute_min"]
            danchi.has_elevator = st["has_elevator"]
            for r in api.get_room_list(did, cond, cfg.wide_filter, "tokyo"):
                room = M.parse_room(r, danchi)
                rooms[room.room_id] = {"danchi_id": did, "name": room.name, "url": room.url,
                                       "rent": room.rent, "commonfee": room.commonfee,
                                       "madori": room.madori, "area": room.area, "floor": room.floor}
    return rooms


# ---- 上新: 富化+打分+通知 ----

def _build_room(rid, info, st):
    return M.Room(room_id=rid, danchi_id=info["danchi_id"], danchi_name=st["name"],
                  name=info["name"], url=info["url"], rent=info["rent"], commonfee=info["commonfee"],
                  madori=info["madori"], area=info["area"], floor=info["floor"], total_floors=0,
                  has_elevator=st["has_elevator"], renovated=False, walk_min=st["walk_min"],
                  commute_min=st["commute_min"], prefecture="tokyo", skcs=st["skcs"])


def run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None):
    if notify_fn is None:
        notify_fn = notify.notify_new_room
    webhook = getattr(getattr(cfg, "discord", None), "webhook_url", "")
    snapshot = load_snapshot(snapshot_path) or {}
    table = _load_table(api, cfg, snapshot)
    cond = build_cond(cfg.destination.station_cd, table,
                      cfg.destination.commute_max_min, cfg.destination.change_max)
    danchi_static = snapshot.get("danchi_static", {})
    rooms = current_rooms(api, cfg, cond, table, danchi_static)
    new_items = diff_new(rooms, snapshot.get("rooms", {}))
    pushed = 0
    failed = set()
    for rid, info in new_items.items():
        st = danchi_static.get(info["danchi_id"])
        if not st:
            failed.add(rid)
            continue
        try:
            room = _build_room(rid, info, st)
            detail = api.get_room_detail(info["danchi_id"], rid)
            M.enrich_room_from_detail(room, detail, cfg.precise.renovated_keywords)
            ok, score, reason = S.should_push(room, cfg)
        except Exception:
            failed.add(rid)   # 失败不进快照 → 下次运行重试
            continue
        if not ok:
            continue
        if notify_fn(webhook, room, score, reason):
            pushed += 1
    snapshot["danchi_static"] = danchi_static
    snapshot["rooms"] = {rid: info for rid, info in rooms.items() if rid not in failed}
    old = load_snapshot(snapshot_path)
    save_snapshot(snapshot_path, snapshot)
    return {"total": len(rooms), "new": len(new_items), "pushed": pushed,
            "changed": old != snapshot}


# ---- 提交快照(仅 Actions; 本地无 GITHUB_TOKEN 跳过) ----

def _commit_snapshot(path, room_count):
    if not os.environ.get("GITHUB_TOKEN"):
        return False
    branch = os.environ.get("GITHUB_REF_NAME") or "main"   # Actions 是 detached HEAD, 先回分支再 push
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "checkout", "-B", branch])
    subprocess.run(["git", "add", path])
    r = subprocess.run(["git", "commit", "-m", f"snapshot: {room_count} rooms"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    subprocess.run(["git", "push", "origin", branch])
    return True


def main():
    cfg = load_config("config.actions.yaml")
    api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
    stat = run(cfg, api)
    print(f"total={stat['total']} new={stat['new']} pushed={stat['pushed']} changed={stat['changed']}",
          flush=True)
    if stat["changed"]:
        _commit_snapshot(SNAPSHOT_PATH, stat["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
