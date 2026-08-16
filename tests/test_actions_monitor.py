import actions_monitor as am
from config import load_config


def test_load_snapshot_missing_returns_none(tmp_path):
    assert am.load_snapshot(str(tmp_path / "nope.json")) is None

def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "rooms.json")
    snap = {"table": {"2354": [2, 0]}, "danchi_static": {"20_2600": {"commute_min": 2}},
            "rooms": {"001080409": {"danchi_id": "20_2600"}}}
    am.save_snapshot(p, snap)
    assert am.load_snapshot(p) == snap

def test_load_snapshot_normalizes_missing_keys(tmp_path):
    p = str(tmp_path / "rooms.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"rooms": {}}')
    s = am.load_snapshot(p)
    assert s["rooms"] == {}
    assert s["table"] == {} and s["danchi_static"] == {}

def test_diff_new_baseline_empty():
    assert am.diff_new({"a": 1}, {}) == {}
    assert am.diff_new({}, {}) == {}

def test_diff_new_finds_new_only():
    cur = {"a": {"danchi_id": "x"}, "b": {"danchi_id": "y"}}
    prev = {"a": {"danchi_id": "x"}}
    assert am.diff_new(cur, prev) == {"b": {"danchi_id": "y"}}

def test_build_cond():
    assert am.build_cond("2827", {"2354": (2, 0), "1000": (30, 1)}, 60, 2) == "2827,1000,2354"

XML = ('<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList>'
       '<stationTo code="2354"><costTime>2</costTime><changeTimes>0</changeTimes></stationTo>'
       '</stationList></trainDoc>').encode("euc-jp")

class FakeApi:
    def __init__(self, danchi_by_area, rooms_by_danchi, details):
        self._d = danchi_by_area; self._r = rooms_by_danchi; self._det = details
    def get_cost_time_xml(self, cd):
        return XML
    def suggest_station(self, name):
        return [{"value": "2354", "text": name}]
    def get_danchi_list(self, area, cond, wide, pref):
        return self._d.get(area, [])
    def get_room_list(self, danchi_id, cond, wide, pref):
        return self._r.get(danchi_id, [])
    def get_room_detail(self, danchi_id, room_id):
        return self._det.get((danchi_id, room_id), {})
    def get_danchi_detail(self, danchi_id):
        return {"facility": "エレベーター"}

DANCHI = [{"id": "20_2600", "name": "館ヶ丘", "skcs": "八王子市", "roomCount": 1,
           "access": "<li>JR中央線「高尾」駅 徒歩10分</li>"}]
ROOMS = [{"id": "001080409", "rent": "60,900円", "type": "3DK", "floorspace": "53㎡", "floor": "4階",
          "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}]
DETAIL = {("20_2600", "001080409"): {"year": "20", "floor": "4階 /5階", "facility": "エレベーター、リフォーム"}}

def make_cfg():
    return load_config("config.actions.yaml")

def test_run_baseline_silent(tmp_path):
    api = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    cfg = make_cfg()
    called = []
    stat = am.run(cfg, api, str(tmp_path / "rooms.json"),
                  notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["total"] == 1 and stat["new"] == 0 and stat["pushed"] == 0
    assert called == []
    s = am.load_snapshot(str(tmp_path / "rooms.json"))
    assert "001080409" in s["rooms"]

def test_run_detects_and_pushes_new(tmp_path):
    p = str(tmp_path / "rooms.json")
    api1 = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    am.run(make_cfg(), api1, p, notify_fn=lambda *a, **k: True)   # 建基线
    rooms2 = ROOMS + [{"id": "0020304", "rent": "70,000円", "type": "2DK", "floorspace": "45㎡",
                       "floor": "2階", "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=0020304"}]
    detail2 = dict(DETAIL); detail2[("20_2600", "0020304")] = {"year": "15", "floor": "2階 /5階",
                                                               "facility": "エレベーター、リフォーム"}
    api2 = FakeApi({"01": DANCHI}, {"20_2600": rooms2}, detail2)
    called = []
    stat = am.run(make_cfg(), api2, p, notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["new"] == 1 and stat["pushed"] == 1 and called == ["0020304"]
    s = am.load_snapshot(p)
    assert set(s["rooms"]) == {"001080409", "0020304"}

def test_detail_failure_skips_snapshot_and_retries(tmp_path):
    p = str(tmp_path / "rooms.json")
    am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), p,
           notify_fn=lambda *a, **k: True)   # 基线只有 001080409
    rooms2 = ROOMS + [{"id": "0020304", "rent": "70,000円", "type": "2DK", "floorspace": "45㎡",
                       "floor": "2階", "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=0020304"}]
    class BadDetail(FakeApi):
        def get_room_detail(self, danchi_id, room_id):
            if room_id == "0020304":
                raise RuntimeError("detail down")
            return DETAIL.get((danchi_id, room_id), {})
    stat = am.run(make_cfg(), BadDetail({"01": DANCHI}, {"20_2600": rooms2}, {}), p,
                  notify_fn=lambda *a, **k: True)
    assert stat["new"] == 1 and stat["pushed"] == 0
    s = am.load_snapshot(p)
    assert "0020304" not in s["rooms"]      # 失败不进快照 → 下次重试
    # 详情恢复后重试成功
    stat2 = am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": rooms2},
                                        {**DETAIL, ("20_2600", "0020304"): {"year": "15", "floor": "2階 /5階",
                                                                             "facility": "エレベーター、リフォーム"}}), p,
                   notify_fn=lambda *a, **k: True)
    assert stat2["new"] == 1 and stat2["pushed"] == 1

def test_notify_failure_retries_next_run(tmp_path):
    p = str(tmp_path / "rooms.json")
    am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), p,
           notify_fn=lambda *a, **k: True)   # 基线
    rooms2 = ROOMS + [{"id": "0020304", "rent": "70,000円", "type": "2DK", "floorspace": "45㎡",
                       "floor": "2階", "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=0020304"}]
    detail2 = {**DETAIL, ("20_2600", "0020304"): {"year": "15", "floor": "2階 /5階",
                                                  "facility": "エレベーター、リフォーム"}}
    api2 = FakeApi({"01": DANCHI}, {"20_2600": rooms2}, detail2)
    # 第一次 notify 失败 → 房间不进快照
    stat1 = am.run(make_cfg(), api2, p, notify_fn=lambda *a, **k: False)
    assert stat1["new"] == 1 and stat1["pushed"] == 0
    assert "0020304" not in am.load_snapshot(p)["rooms"]
    # 第二次 notify 成功 → 房间被再次发现并推送
    stat2 = am.run(make_cfg(), api2, p, notify_fn=lambda *a, **k: True)
    assert stat2["new"] == 1 and stat2["pushed"] == 1

def test_load_snapshot_non_dict_returns_none(tmp_path):
    p = str(tmp_path / "rooms.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("[]")
    assert am.load_snapshot(p) is None


# ---- top-X 推送: 过硬条件后按分数取前 N ----

def _mk_rooms(specs):
    """specs: [(room_id, rent_str), ...] → (room list, detail dict)"""
    rooms = [{"id": rid, "rent": rent, "type": "2DK", "floorspace": "45㎡", "floor": "2階",
              "urlDetail": f"/chintai/kanto/tokyo/20_2600_room.html?JKSS={rid}"}
             for rid, rent in specs]
    details = {("20_2600", rid): {"year": "15", "floor": "2階 /5階", "facility": "エレベーター、リフォーム"}
               for rid, _ in specs}
    return rooms, details


def test_run_pushes_top_n_by_score(tmp_path):
    p = str(tmp_path / "rooms.json")
    cfg = make_cfg(); cfg.push_top_n = 2
    am.run(cfg, FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), p,
           notify_fn=lambda *a, **k: True)   # 基线: 1 间既有房
    rooms, details = _mk_rooms([("A", "60,000円"), ("B", "70,000円"), ("C", "80,000円")])
    called = []
    stat = am.run(cfg, FakeApi({"01": DANCHI}, {"20_2600": ROOMS + rooms}, {**DETAIL, **details}), p,
                  notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["new"] == 3 and stat["pushed"] == 2
    assert called == ["A", "B"]   # 分数最高两名(租金最低分最高)


def test_run_hard_pass_filters_candidates(tmp_path):
    p = str(tmp_path / "rooms.json")
    cfg = make_cfg(); cfg.push_top_n = 3
    am.run(cfg, FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), p,
           notify_fn=lambda *a, **k: True)   # 基线: 1 间既有房
    rooms, details = _mk_rooms([("A", "60,000円"), ("B", "70,000円"), ("X", "150,000円")])
    called = []
    stat = am.run(cfg, FakeApi({"01": DANCHI}, {"20_2600": ROOMS + rooms}, {**DETAIL, **details}), p,
                  notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["new"] == 3 and stat["pushed"] == 2
    assert "X" not in called   # 租金>10万 不过硬条件, 不参与 top-X


# ---- 快照存储抽象(FileStore / 可注入 store) ----

class FakeStore:
    def __init__(self, initial=None):
        self.data = initial
        self.loads = 0
        self.saves = 0
    def load(self):
        self.loads += 1
        return self.data
    def save(self, snapshot):
        self.saves += 1
        self.data = snapshot

def test_run_uses_injectable_store():
    cfg = make_cfg()
    store = FakeStore()
    api = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    stat = am.run(cfg, api, store=store, notify_fn=lambda *a, **k: True)
    assert stat["total"] == 1 and stat["new"] == 0 and stat["pushed"] == 0
    assert store.loads >= 2 and store.saves >= 1          # 开头 load + changed 判定 load, 末尾 save
    assert "001080409" in store.data["rooms"]
    assert "20_2600" in store.data["danchi_static"]

def test_run_default_store_still_writes_file(tmp_path):
    p = str(tmp_path / "rooms.json")
    am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), str(p),
           notify_fn=lambda *a, **k: True)
    assert "001080409" in am.load_snapshot(p)["rooms"]

