# tests/test_monitor.py
import logging
import time

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

# ---- F1：推送失败不得标记 seen，必须下次重试 ----

def test_push_failure_not_marked_seen_and_retried():
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    db = DB(":memory:"); db.init()
    calls = []
    def notify_down(url, room, score, reason):
        calls.append(room.room_id); return False  # Discord 暂时挂掉
    stat1 = monitor.run_monitor(make_cfg(), api, db, notify_fn=notify_down, comment_fn=lambda r: "")
    assert stat1["pushed"] == 0
    assert db.is_new_room("001080409") is True  # 推送失败：不标记 → 下次重试
    def notify_ok(url, room, score, reason):
        calls.append(room.room_id); return True  # Discord 恢复
    stat2 = monitor.run_monitor(make_cfg(), api, db, notify_fn=notify_ok, comment_fn=lambda r: "")
    assert stat2["pushed"] == 1
    assert len(calls) == 2

# ---- F3：通勤/电梯读库，不再每轮调 suggest/detail ----

class StaticBlockingApi(FakeApi):
    """静态信息已入 DB 后，若仍调 suggest_station/get_danchi_detail 则报错。"""
    def suggest_station(self, name):
        raise AssertionError("不应再调用 suggest_station（应读 DB 缓存）")
    def get_danchi_detail(self, danchi_id):
        raise AssertionError("不应再调用 get_danchi_detail（应读 DB 缓存）")

def test_run_monitor_reads_static_from_db():
    db = DB(":memory:"); db.init()
    db.upsert_danchi_from_search(DANCHI[0])
    db.set_danchi_static("20_2600", 30, True)
    pushed = []
    def fake_notify(url, room, score, reason):
        pushed.append(room.room_id); return True
    stat = monitor.run_monitor(make_cfg(),
                               StaticBlockingApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL),
                               db, notify_fn=fake_notify, comment_fn=lambda r: "")
    assert stat["pushed"] == 1
    assert db.get_danchi_static("20_2600") == (30, True)

def test_run_monitor_caches_static_from_live_fallback():
    # 冷启动（DB 无静态信息）：在线解析并写回，后续轮询直接读库
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    db = DB(":memory:"); db.init()
    monitor.run_monitor(make_cfg(), api, db, notify_fn=lambda url, room, score, reason: True,
                        comment_fn=lambda r: "")
    # suggest→2354（costTime=2）；团地详情含エレベーター
    assert db.get_danchi_static("20_2600") == (2, True)

# ---- F4：异常记入 stat 并打 warning 日志 ----

class BoomApi(FakeApi):
    def get_danchi_list(self, area, cond, wide, pref):
        raise RuntimeError("api down")

def test_run_monitor_logs_errors_as_warnings(caplog):
    db = DB(":memory:"); db.init()
    with caplog.at_level(logging.WARNING, logger="monitor"):
        stat = monitor.run_monitor(make_cfg(), BoomApi({}, {}, {}), db)
    assert stat["errors"] == ["area 01: api down"]
    assert any(r.name == "monitor" and r.levelno == logging.WARNING for r in caplog.records)

# ---- F6：房间详情失败 → 不推也不标记（下次重试）----

class BadDetailApi(FakeApi):
    def get_room_detail(self, danchi_id, room_id):
        raise RuntimeError("detail down")

def test_detail_failure_skips_room_and_retries():
    api = BadDetailApi({"01":DANCHI}, {"20_2600":ROOMS}, {})
    db = DB(":memory:"); db.init()
    pushed = []
    def fake_notify(url, room, score, reason):
        pushed.append(room.room_id); return True
    stat = monitor.run_monitor(make_cfg(), api, db, notify_fn=fake_notify, comment_fn=lambda r: "")
    assert stat["pushed"] == 0
    assert db.is_new_room("001080409") is True  # 数据未验证：不推、不标记

def test_run_monitor_records_poll_even_no_new():
    api = FakeApi({"01":DANCHI}, {"20_2600":[]}, {})
    db = DB(":memory:"); db.init()
    stat = monitor.run_monitor(make_cfg(), api, db)
    assert stat["new_rooms"] == 0
    assert len(db.fetch_poll_log("20_2600", 10)) == 1

# ---- F2：cost-time XML 静态文件缓存 ≤24h ----

def test_cost_time_fetched_at_most_once_per_day():
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    api.xml_calls = 0
    orig_get = api.get_cost_time_xml
    def counting_get(cd):
        api.xml_calls += 1
        return orig_get(cd)
    api.get_cost_time_xml = counting_get
    db = DB(":memory:"); db.init()
    monitor._cost_time_cache = {}  # 隔离其它用例留下的缓存
    monitor.run_monitor(make_cfg(), api, db)
    monitor.run_monitor(make_cfg(), api, db)
    assert api.xml_calls == 1  # 同一天内两次轮询只下载一次
    # 缓存过期（>24h）后重新下载
    entry = monitor._cost_time_cache["2827"]
    monitor._cost_time_cache["2827"] = (0.0, entry[1], entry[2])
    monitor.run_monitor(make_cfg(), api, db)
    assert api.xml_calls == 2
