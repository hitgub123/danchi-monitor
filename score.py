# score.py
from models import Room

def hard_pass(room: Room, precise, commute_max_min: int = 60) -> bool:
    if room.commute_min > commute_max_min:
        return False
    if room.rent > precise.rent_max:
        return False
    if room.area < precise.area_min:
        return False
    if room.walk_min > precise.walk_max:
        return False
    if room.floor >= precise.elevator_min_floor and not room.has_elevator:
        return False
    return True

def score_room(room: Room, weights, commute_max_min: int = 60) -> float:
    commute_limit = max(float(commute_max_min), 1.0)
    commute = weights.commute * max(0.0, 1 - room.commute_min / commute_limit)
    if room.walk_min <= 10:
        walk = weights.walk
    elif room.walk_min <= 15:
        walk = weights.walk - 2.8 * (room.walk_min - 10)
    else:
        walk = 0.0
    if room.rent <= 0:
        rent = weights.rent * 0.5
    else:
        rent = weights.rent * max(0.0, 1 - room.rent / 100000)
    area = weights.area * max(0.0, min(1.0, (room.area - 40) / 40))
    room_type = weights.room_type if ("LDK" in room.madori) else weights.room_type * 0.4
    if room.floor <= 0:
        floor = 0.0
    elif room.floor <= 2:
        floor = weights.floor
    else:
        floor = weights.floor * max(0.0, (6 - room.floor) / 4)
    tokyo = weights.tokyo if room.prefecture == "tokyo" else 0
    return round(commute + walk + rent + area + room_type + floor + tokyo, 1)

def baseline_score(cfg) -> float:
    b = cfg.baseline
    r = Room(room_id="b", danchi_id="", danchi_name="金町", name="", url="",
             rent=b.rent, commonfee=0, madori=b.madori, area=b.area,
             floor=b.floor, total_floors=0, has_elevator=False, renovated=False,
             walk_min=b.walk, commute_min=b.commute, prefecture="tokyo", skcs="")
    commute_max = getattr(getattr(cfg, "destination", None), "commute_max_min", 60)
    return score_room(r, cfg.weights, commute_max)

def should_push(room: Room, cfg) -> tuple:
    commute_max = getattr(getattr(cfg, "destination", None), "commute_max_min", 60)
    if not hard_pass(room, cfg.precise, commute_max):
        return False, 0.0, "未通过硬条件"
    s = score_room(room, cfg.weights, commute_max)
    thr = getattr(cfg, "push_threshold", None)
    if thr is None:
        thr = baseline_score(cfg) if cfg.baseline is not None else 0.0
    if s >= thr:
        reasons = []
        if room.walk_min <= cfg.precise.walk_ideal:
            reasons.append(f"步行{room.walk_min}分(≤{cfg.precise.walk_ideal}理想)")
        reasons.append(f"月租{room.rent:,}円 面积{room.area:.0f}㎡ {room.madori}")
        if room.has_elevator:
            reasons.append("有电梯")
        return True, s, "；".join(reasons)
    return False, s, "未超过推送阈值"
