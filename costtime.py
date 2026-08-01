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
