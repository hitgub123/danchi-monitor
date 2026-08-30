# show_current.py — 评估当前在架的所有空房并打分（诊断/测试推送用）
# 不标记 seen、不写 poll_log，纯粹看"现在有哪些房间、多少分"
import logging
logging.basicConfig(level=logging.WARNING)
from config import load_config
from ur_api import UrApi
from db import DB
import monitor as M
import models as MM
import score as S

cfg = load_config("config.yaml")
db = DB("data.db"); db.init()
api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)

table, cond = M._load_cost_time(cfg, api)  # 复用 monitor 的 cost-time 缓存

results = []  # (score, would_push, reason, room)
for area in cfg.areas:
    for d in api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo"):
        if int(d.get("roomCount") or 0) <= 0:
            continue
        try:
            danchi = MM.parse_danchi(d, "tokyo")
            static = db.get_danchi_static(d["id"])
            if static is None:
                import costtime
                danchi.commute_min = costtime.resolve_commute_min(danchi.station_name, api, table)
                d_detail = api.get_danchi_detail(d["id"])
                danchi.has_elevator = "エレベーター" in (d_detail.get("facility") or "")
            else:
                danchi.commute_min, danchi.has_elevator = static
            rooms = api.get_room_list(d["id"], cond, cfg.wide_filter, "tokyo")
        except Exception as e:
            logging.getLogger("show").warning("团地 %s 失败: %s", d["id"], e)
            continue
        for r in rooms:
            room = MM.parse_room(r, danchi)
            try:
                detail = api.get_room_detail(d["id"], room.room_id)
                MM.enrich_room_from_detail(room, detail, cfg.precise.renovated_keywords)
            except Exception:
                continue  # 详情失败跳过
            ok, score, reason = S.should_push(room, cfg)
            results.append((score, ok, reason, room))

results.sort(key=lambda x: -x[0])
print(f"共评估 {len(results)} 个在架房间")
pushed = [x for x in results if x[1]]
print(f"通过硬条件且分数>阈值(当前会推送): {len(pushed)} 个\n")
print("== 会推送的房间（按分高到低）==")
for score, ok, reason, room in pushed:
    print(f"{score:.1f}分 | {room.danchi_name} {room.name} | {room.madori} {room.area:.0f}㎡ {room.rent:,}円 | 步行{room.walk_min}分 通勤{room.commute_min}分 | {room.url}")
print("\n== 40分以上但被硬条件挡住的（仅作参考）==")
for score, ok, reason, room in results:
    if score > 40 and not ok:
        print(f"{score:.1f}分 | {room.danchi_name} {room.name} | {reason}")
