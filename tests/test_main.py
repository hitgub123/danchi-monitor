# tests/test_main.py
import main

def test_pick_interval_day():
    cfg = type("S", (), {"day_interval_min":5,"night_interval_min":30})()
    assert main.pick_interval(cfg, 10) == 5  # 10点=日间

def test_pick_interval_night():
    cfg = type("S", (), {"day_interval_min":5,"night_interval_min":30})()
    assert main.pick_interval(cfg, 23) == 30
    assert main.pick_interval(cfg, 3) == 30
