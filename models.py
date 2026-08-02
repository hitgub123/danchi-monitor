# models.py
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

def parse_rent(s: str) -> int:
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0

def parse_area(s: str) -> float:
    # UR API 面积单位可能是 ㎡ 或 HTML 实体 &#13217;（U+33A1）
    m = re.search(r"([\d.]+)\s*(?:㎡|&#\d+;)", s or "")
    return float(m.group(1)) if m else 0.0

def parse_floor(s: str) -> Tuple[int, int]:
    if not s:
        return (0, 0)
    nums = [int(x) for x in re.findall(r"(\d+)階", s)]
    if len(nums) >= 2:
        return (nums[0], nums[1])
    return (nums[0], 0) if nums else (0, 0)

def parse_access(access_html: str) -> List[dict]:
    entries = []
    for li in re.findall(r"<li>(.*?)</li>", access_html or ""):
        station = re.search(r"[「｢]([^」｣]+)[」｣]駅", li)
        walk_m = re.findall(r"徒歩(\d+)", li)
        bus_m = re.search(r"バス(\d+)分", li)
        if not station:
            continue
        walk = min(int(x) for x in walk_m) if walk_m else (int(bus_m.group(1)) if bus_m else 99)
        entries.append({"station_name": station.group(1), "walk": walk})
    return entries

@dataclass
class Danchi:
    danchi_id: str
    name: str
    skcs: str
    room_count: int
    prefecture: str
    station_name: str = ""
    walk_min: int = 99
    has_elevator: bool = False
    commute_min: int = 60

@dataclass
class Room:
    room_id: str
    danchi_id: str
    danchi_name: str
    name: str
    url: str
    rent: int
    commonfee: int
    madori: str
    area: float
    floor: int
    total_floors: int
    has_elevator: bool
    renovated: bool
    walk_min: int
    commute_min: int
    prefecture: str
    skcs: str
    year: int = 0
    facility: str = ""

def parse_danchi(d: dict, prefecture: str) -> Danchi:
    access = parse_access(d.get("access") or "")
    station_name = access[0]["station_name"] if access else ""
    walk_min = access[0]["walk"] if access else 99
    return Danchi(
        danchi_id=d["id"], name=d.get("name") or "", skcs=d.get("skcs") or "",
        room_count=int(d.get("roomCount") or 0), prefecture=prefecture,
        station_name=station_name, walk_min=walk_min,
    )

def _full_url(u: str) -> str:
    """UR API 返回的 urlDetail 是相对路径, 补全为完整链接。"""
    return u if (not u or u.startswith("http")) else "https://www.ur-net.go.jp" + u

def parse_room(r: dict, danchi: Danchi) -> Room:
    floor, total = parse_floor(r.get("floor"))
    return Room(
        room_id=r["id"], danchi_id=danchi.danchi_id, danchi_name=danchi.name,
        name=r.get("name") or "", url=_full_url(r.get("urlDetail") or ""),
        rent=parse_rent(r.get("rent")), commonfee=parse_rent(r.get("commonfee")),
        madori=r.get("type") or "", area=parse_area(r.get("floorspace")),
        floor=floor, total_floors=total, has_elevator=danchi.has_elevator,
        renovated=False, walk_min=danchi.walk_min, commute_min=danchi.commute_min,
        prefecture=danchi.prefecture, skcs=danchi.skcs,
    )

def enrich_room_from_detail(room: Room, detail: dict, renovated_keywords: list) -> None:
    room.year = int(re.search(r"\d+", detail.get("year") or "0").group())
    floor, total = parse_floor(detail.get("floor"))
    if total:
        room.total_floors = total
    facility = detail.get("facility") or ""
    room.facility = facility
    # 注意：电梯在团地级，不在房间级 —— 由 danchi.has_elevator 流入 room（parse_room），此处不改写
    room.renovated = any(k in (facility + (detail.get("feature") or "")) for k in renovated_keywords)
