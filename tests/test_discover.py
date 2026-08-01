# tests/test_discover.py
import json
import discover
from ur_api import UrApi
from db import DB

class FakeApi:
    def __init__(self, cost_xml, danchi_by_area):
        self._xml = cost_xml
        self._d = danchi_by_area
        self.calls = []
    def get_cost_time_xml(self, station_cd):
        self.calls.append(("xml", station_cd))
        return self._xml
    def get_danchi_list(self, area, cond, wide, pref):
        self.calls.append(("list", area))
        return self._d.get(area, [])

SAMPLE_XML = '<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList><stationTo code="2354"><stationName>新橋</stationName><costTime>2</costTime><changeTimes>0</changeTimes></stationTo></stationList></trainDoc>'.encode("euc-jp")

def make_cfg():
    class C: pass
    c = C()
    c.areas = ["01"]
    c.prefectures = ["tokyo"]
    c.destination = type("D", (), {"station_cd":"2827","commute_max_min":60,"change_max":2})()
    c.wide_filter = None
    return c

def test_run_discover_adds_new_danchi():
    api = FakeApi(SAMPLE_XML, {"01":[{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}]})
    db = DB(":memory:"); db.init()
    n = discover.run_discover(make_cfg(), api, db)
    assert n == 1
    assert db.is_target_danchi("20_2600")

def test_run_discover_idempotent():
    api = FakeApi(SAMPLE_XML, {"01":[{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":10}]})
    db = DB(":memory:"); db.init()
    discover.run_discover(make_cfg(), api, db)
    n = discover.run_discover(make_cfg(), api, db)
    assert n == 0  # 第二次无新增
