# costtime.py
import re
import xml.etree.ElementTree as ET

def _fix_encoding(raw: bytes) -> bytes:
    text = raw.decode("euc-jp")
    text = re.sub(r'encoding="[^"]*"', 'encoding="utf-8"', text, count=1)
    return text.encode("utf-8")

def parse_cost_time(xml_bytes: bytes, cost_max: int, change_max: int) -> dict:
    root = ET.fromstring(_fix_encoding(xml_bytes))
    table = {}
    for st in root.iter("stationTo"):
        cost = int(st.find("costTime").text)
        change = int(st.find("changeTimes").text)
        if cost <= cost_max and change <= change_max:
            table[st.get("code")] = (cost, change)
    return table

def build_station_condition(dest_cd: str, table: dict, cost_max: int, change_max: int) -> str:
    codes = sorted(table.keys())
    return dest_cd + "," + ",".join(codes)

def resolve_commute_min(station_name: str, api, table: dict) -> int:
    """通过站名反查 station_cd → 通勤时间。查不到返回 60（0 分，最坏情况）。"""
    try:
        if not station_name:
            return 60
        for h in api.suggest_station(station_name):
            cd = str(h["value"])
            if cd in table:
                return table[cd][0]
    except Exception:
        pass
    return 60
