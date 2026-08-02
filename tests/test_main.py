# tests/test_main.py
import logging

import pytest
import main

def _fake_cfg():
    # 与真实 Config 形状一致：day_interval_min/night_interval_min/poll_log_keep_days 嵌套在 .schedule 下
    return type("S", (), {"schedule": type("Sch", (), {"day_interval_min":5,"night_interval_min":30,"poll_log_keep_days":90})()})()

class _FakeDB:
    """_loop 需要的最小 DB 接口：count_danchi / get_meta / set_meta / prune_poll_log。"""
    def count_danchi(self):
        return 1  # 非空库，跳过启动时 bootstrap
    def get_meta(self, key):
        return None  # 未记录 → 触发一次 discover
    def set_meta(self, key, value):
        pass
    def prune_poll_log(self, keep_days):
        return 0

def test_pick_interval_day():
    cfg = _fake_cfg()
    assert main.pick_interval(cfg.schedule, 10) == 5  # 10点=日间

def test_pick_interval_night():
    cfg = _fake_cfg()
    assert main.pick_interval(cfg.schedule, 23) == 30
    assert main.pick_interval(cfg.schedule, 3) == 30

def test_loop_wiring_passes_schedule(monkeypatch):
    """回归测试：_loop 必须把 cfg.schedule 传给 pick_interval，否则首轮后 AttributeError 崩溃"""
    called = {}
    cfg = _fake_cfg()
    db = _FakeDB()
    api = object()

    def fake_discover(cfg_, api_, db_):
        return 0

    def fake_monitor(cfg_, api_, db_):
        return {}

    def fake_sleep(sec):
        called["sec"] = sec
        raise RuntimeError("stop_loop")

    monkeypatch.setattr("discover.run_discover", fake_discover)
    monkeypatch.setattr("monitor.run_monitor", fake_monitor)
    monkeypatch.setattr("main.time.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop_loop"):
        main._loop(cfg, db, api)
    # 能走到 sleep 即证明 pick_interval(cfg.schedule, ...) 未抛 AttributeError。
    # 间隔取决于当前小时（日间5min 或 夜间30min），加上 jitter 0~5s。
    day_lo, day_hi = 5 * 60, 5 * 60 + 5
    night_lo, night_hi = 30 * 60, 30 * 60 + 5
    assert (day_lo <= called["sec"] <= day_hi) or (night_lo <= called["sec"] <= night_hi)

def test_loop_logs_monitor_error_count(monkeypatch, caplog):
    # F4：_loop 必须把 run_monitor 的 stat（含错误数）打进日志
    cfg = _fake_cfg()

    def fake_discover(cfg_, api_, db_):
        return 0

    def fake_monitor(cfg_, api_, db_):
        return {"danchi_checked": 3, "new_rooms": 1, "pushed": 0, "errors": ["boom"]}

    def fake_sleep(sec):
        raise RuntimeError("stop_loop")

    monkeypatch.setattr("discover.run_discover", fake_discover)
    monkeypatch.setattr("monitor.run_monitor", fake_monitor)
    monkeypatch.setattr("main.time.sleep", fake_sleep)

    with caplog.at_level(logging.INFO, logger="main"):
        with pytest.raises(RuntimeError, match="stop_loop"):
            main._loop(cfg, _FakeDB(), object())
    msgs = [r.message for r in caplog.records if r.name == "main"]
    assert any("errors=1" in m for m in msgs)  # 错误数必须在日志里可见
