# models.py
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

def parse_rent(s: str) -> int:
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0

def parse_area(s: str) -> float:
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
        parts = re.split(r"[「｢]([^」｣]+)[」｣]駅", li)
        for i in range(1, len(parts), 2):
            station = parts[i].strip()
            after = parts[i + 1] if i + 1 < len(parts) else ""
            if re.search(r"バス\d+(?:～\d+)?分", after):
                entries.append({"station_name": station, "walk": 99, "has_bus": True})
            else:
                walk_m = [int(x) for x in re.findall(r"徒歩(\d+)", after)]
                if walk_m:
                    entries.append({"station_name": station, "walk": min(walk_m), "has_bus": False})
    entries.sort(key=lambda e: (e["has_bus"], e["walk"]))
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
    online_apply: bool = False
    kari_link: str = ""

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
    year_match = re.search(r"\d+", detail.get("year") or "")
    room.year = int(year_match.group()) if year_match else 0
    floor, total = parse_floor(detail.get("floor"))
    if total:
        room.total_floors = total
    facility = detail.get("facility") or ""
    room.facility = facility
    room.has_elevator = ("エレベーター" in facility) or (room.total_floors > 5)
    text = facility + (detail.get("feature") or "")
    room.renovated = any(k in text for k in (renovated_keywords or []))
    room.kari_link = detail.get("kariLink") or ""
    room.online_apply = bool(room.kari_link.strip())
    if detail.get("roomNm"):
        room.name = detail.get("roomNm")
