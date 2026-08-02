# tests/test_score.py
from models import Room
from score import hard_pass, score_room, should_push, baseline_score

def make_room(**kw):
    base = dict(room_id="r1", danchi_id="20_2600", danchi_name="館ヶ丘", name="409号室",
                url="/x", rent=60900, commonfee=4500, madori="3DK", area=53.0,
                floor=4, total_floors=5, has_elevator=True, renovated=True,
                walk_min=10, commute_min=40, prefecture="tokyo", skcs="八王子市",
                year=20, facility="エレベーター、リフォーム")
    base.update(kw)
    return Room(**base)

class Precise:
    rent_max=100000; area_min=40; walk_max=15; walk_ideal=10
    elevator_min_floor=3; year_max=30; renovated_keywords=["リフォーム"]

class Weights:
    commute=30; walk=20; rent=20; area=15; room_type=5; floor=5; tokyo=5

def test_hard_pass_ok():
    assert hard_pass(make_room(), Precise())

def test_hard_pass_rejects_high_floor_no_elevator():
    r = make_room(floor=3, has_elevator=False)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_too_old():
    r = make_room(year=51, renovated=False)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_too_expensive():
    r = make_room(rent=120000)
    assert not hard_pass(r, Precise())

def test_hard_pass_rejects_small():
    r = make_room(area=30)
    assert not hard_pass(r, Precise())

def test_score_better_than_baseline():
    good = make_room()
    baseline = make_room(rent=95000, area=43, walk_min=2, commute_min=30, madori="1DK",
                         floor=5, has_elevator=False, renovated=False)
    assert score_room(good, Weights()) > score_room(baseline, Weights())

def test_should_push_flags_new():
    r = make_room()
    # baseline_score < good score → push
    ok, s, reason = should_push(r, type("Cfg", (), {"precise": Precise(), "weights": Weights(),
                                                    "baseline": None})())
    assert ok
    assert 0 <= s <= 100
    assert reason

def test_should_push_uses_push_threshold():
    r = make_room()  # 默认分约52，通过硬条件
    cfg = type("Cfg", (), {"precise": Precise(), "weights": Weights(), "baseline": None,
                           "push_threshold": 40})()
    ok, s, _ = should_push(r, cfg)
    assert ok and s > 40
    cfg_high = type("Cfg", (), {"precise": Precise(), "weights": Weights(), "baseline": None,
                                "push_threshold": 99})()
    ok2, _, reason = should_push(r, cfg_high)
    assert not ok2
    assert "未超过推送阈值" in reason

def test_floor_zero_unknown_gets_no_score():
    # 回归：floor<=0（解析失败/未知）不得当 1-2 楼给满分，应与 ≥6 楼同记 0 分
    w = Weights()
    f0 = score_room(make_room(floor=0, has_elevator=True), w)
    f1 = score_room(make_room(floor=1, has_elevator=True), w)
    f6 = score_room(make_room(floor=6, has_elevator=True), w)
    assert f1 - f0 == w.floor          # floor=0 无楼层分，floor=1 有满分
    assert f0 == f6                     # 未知楼层与 ≥6 楼同分（0 分）

def test_floor_score_lower_floor_better():
    w = Weights()
    f1 = score_room(make_room(floor=1, has_elevator=True), w)
    f2 = score_room(make_room(floor=2, has_elevator=True), w)
    f3 = score_room(make_room(floor=3, has_elevator=True), w)
    f4 = score_room(make_room(floor=4, has_elevator=True), w)
    f5 = score_room(make_room(floor=5, has_elevator=True), w)
    f6 = score_room(make_room(floor=6, has_elevator=True), w)
    f10 = score_room(make_room(floor=10, has_elevator=True), w)
    # 1-2楼满分
    assert f1 == f2
    # 低楼层更高分（单调非增），1楼高于4楼
    assert f1 > f4
    assert f2 > f3 > f4 > f5 > f6
    # ≥6楼记0：与10楼同分
    assert f6 == f10
