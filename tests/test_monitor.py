# tests/test_monitor.py
import monitor
from db import DB

class FakeApi:
    def __init__(self, danchi_by_area, rooms_by_danchi, details):
        self._d = danchi_by_area; self._r = rooms_by_danchi; self._det = details
    def get_cost_time_xml(self, cd):
        return '<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList><stationTo code="2354"><costTime>2</costTime><changeTimes>0</changeTimes></stationTo></stationList></trainDoc>'.encode("euc-jp")
    def suggest_station(self, name):
        return [{"value": "2354", "text": name}]  # 站名→code，落入通勤表
    def get_danchi_list(self, area, cond, wide, pref):
        return self._d.get(area, [])
    def get_room_list(self, danchi_id, cond, wide, pref):
        return self._r.get(danchi_id, [])
    def get_room_detail(self, danchi_id, room_id):
        return self._det.get((danchi_id, room_id), {})
    def get_danchi_detail(self, danchi_id):
        return {"facility": "エレベーター"}  # 电梯在团地级

DANCHI = [{"id":"20_2600","name":"館ヶ丘","skcs":"八王子市","roomCount":1,
           "access":"<li>JR中央線「高尾」駅 徒歩10分</li>"}]
ROOMS = [{"id":"001080409","rent":"60,900円","type":"3DK","floorspace":"53㎡","floor":"4階",
          "urlDetail":"/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}]
DETAIL = {("20_2600","001080409"): {"year":"20","floor":"4階 /5階",
            "facility":"エレベーター、リフォーム"}}

def make_cfg():
    class P: rent_max=100000; area_min=40; walk_max=15; walk_ideal=10; elevator_min_floor=3; year_max=30; renovated_keywords=["リフォーム"]
    class W: commute=30; walk=20; rent=20; area=15; room_type=5; floor=5; tokyo=5
    class B: rent=95000; area=43; walk=2; commute=30; madori="1DK"; western=False; floor=5
    class Dest: station_cd="2827"; commute_max_min=60; change_max=2
    class C: pass
    c = C()
    c.destination = Dest(); c.areas=["01"]; c.prefectures=["tokyo"]
    c.wide_filter=None; c.precise=P(); c.weights=W(); c.baseline=B()
    return c

def test_run_monitor_pushes_new_room():
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    db = DB(":memory:"); db.init()
    pushed = []
    def fake_notify(url, room, score, reason):
        pushed.append((room.room_id, score)); return True
    def fake_comment(room, **kw):
        return "不错"
    stat = monitor.run_monitor(make_cfg(), api, db, notify_fn=fake_notify, comment_fn=fake_comment)
    assert stat["new_rooms"] == 1
    assert stat["pushed"] == 1
    assert pushed[0][0] == "001080409"
    # 幂等：第二次不再推
    stat2 = monitor.run_monitor(make_cfg(), api, db, notify_fn=fake_notify, comment_fn=fake_comment)
    assert stat2["new_rooms"] == 0
    # poll_log 已写
    assert len(db.fetch_poll_log("20_2600", 10)) == 2

def test_run_monitor_records_poll_even_no_new():
    api = FakeApi({"01":DANCHI}, {"20_2600":[]}, {})
    db = DB(":memory:"); db.init()
    stat = monitor.run_monitor(make_cfg(), api, db)
    assert stat["new_rooms"] == 0
    assert len(db.fetch_poll_log("20_2600", 10)) == 1
