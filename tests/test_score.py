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
