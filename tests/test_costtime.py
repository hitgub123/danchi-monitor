# tests/test_costtime.py
from costtime import parse_cost_time, build_station_condition

SAMPLE = """<?xml version="1.0" encoding="euc-jp"?>
<trainDoc status="0"><condition><stationFrom code="2827">
<stationName>浜松町</stationName></stationFrom>
<TimeNmin>60</TimeNmin></condition>
<stationList>
  <stationTo code="2354"><stationName>新橋</stationName>
    <costTime>2</costTime><changeTimes>0</changeTimes></stationTo>
  <stationTo code="9999"><stationName>遠方</stationName>
    <costTime>70</costTime><changeTimes>2</changeTimes></stationTo>
  <stationTo code="8888"><stationName>乗換多</stationName>
    <costTime>40</costTime><changeTimes>5</changeTimes></stationTo>
</stationList></trainDoc>"""

def test_parse_filters_by_cost_and_change():
    table = parse_cost_time(SAMPLE.encode("euc-jp"), 60, 2)
    assert table["2354"] == (2, 0)
    assert "9999" not in table  # cost 70 > 60
    assert "8888" not in table  # change 5 > 2

def test_build_station_condition():
    table = parse_cost_time(SAMPLE.encode("euc-jp"), 60, 2)
    cond = build_station_condition("2827", table, 60, 2)
    assert cond.startswith("2827,")
    assert "2354" in cond
