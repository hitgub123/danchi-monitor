# tests/test_main.py
import logging

import pytest
import main
from datetime import datetime


def _fake_cfg():
    # 与真实 Config 形状一致，schedule 含新字段
    class S:
        day_interval_min = 10
        night_interval_min = 60
        day_start_hour = 8
        day_end_hour = 22
        dense_windows = None
        poll_log_keep_days = 90
        max_jitter_sec = 5
    return type("C", (), {"schedule": S()})()


class _FakeDB:
    """_loop 需要的最小 DB 接口：count_danchi / get_meta / set_meta / prune_poll_log。"""
    def count_danchi(self):
        return 1  # 非空库，跳过启动时 bootstrap
    def get_meta(self, key):
        if key == "last_stats":
            return "9999999999"  # 未来时间戳 → 月度统计不触发(假DB无 conn)
        return None  # last_discover 未记录 → 触发一次 discover
    def set_meta(self, key, value):
        pass
    def prune_poll_log(self, keep_days):
        return 0


def test_next_sleep_sec_returns_seconds_until_target(monkeypatch):
    class FakeSched:
        def next_poll_at(self, now):
            return datetime(2026, 8, 15, 12, 30)
    monkeypatch.setattr("random.uniform", lambda a, b: 2.5)
    now = datetime(2026, 8, 15, 12, 20)
    # 12:30 - 12:20 = 600s + jitter 2.5
    assert main._next_sleep_sec(FakeSched(), now, 5) == 600 + 2.5


def test_loop_wiring_builds_schedule_and_sleeps(monkeypatch):
    """回归测试：_loop 必须用 cfg.schedule 构造调度器并睡到下一网格点，否则首轮后崩溃"""
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
    monkeypatch.setattr("main._next_sleep_sec", lambda sched, now, jitter: 42)

    with pytest.raises(RuntimeError, match="stop_loop"):
        main._loop(cfg, db, api)
    assert called["sec"] == 42  # 走到了 sleep，且用的是调度器算出的秒数


def test_loop_logs_monitor_error_count(monkeypatch, caplog):
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
    monkeypatch.setattr("main._next_sleep_sec", lambda sched, now, jitter: 42)

    with caplog.at_level(logging.INFO, logger="main"):
        with pytest.raises(RuntimeError, match="stop_loop"):
            main._loop(cfg, _FakeDB(), object())
    msgs = [r.message for r in caplog.records if r.name == "main"]
    assert any("errors=1" in m for m in msgs)
